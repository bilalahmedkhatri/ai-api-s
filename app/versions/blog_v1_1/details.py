"""One batch asset lookup; store validated image details outside SEO metadata."""

import copy
from urllib.parse import urlsplit

import sqlalchemy as sa
from app.blog.content import MEDIA_TYPES, document_nodes, safe_url
from app.blog.schema import blog_assets as assets
from app.versions.blog_v1_1.contracts import BlogDetails, ImageDetails
from fastapi import HTTPException


async def bind_details(db, values, supplied=None, *, uploader_id=None):
    document = copy.deepcopy(values["content_json"])
    nodes = [node for node in document_nodes(document) if node["type"] in MEDIA_TYPES]
    images = [node for node in nodes if node["type"] in {"image", "customImage"}]
    if len(images) > 3:
        raise HTTPException(422, "A blog may contain at most three content images.")
    if supplied is not None:
        details = BlogDetails.model_validate(supplied).model_dump()
        featured = details["featured_image"]
        requested_cover = featured["asset_id"] if featured else None
        if "cover_asset_id" in values and values["cover_asset_id"] != requested_cover:
            raise HTTPException(422, "Featured image and cover asset must match.")
        values["cover_asset_id"] = requested_cover
    else:
        previous = values.get("blog_details_json") or {}
        details = {
            "featured_image": previous.get("featured_image"),
            "content_images": previous.get("content_images", []),
        }
        # Only the server-generated URL field is removed when revalidating saved details.
        details = {
            "featured_image": (
                {k: v for k, v in details["featured_image"].items() if k != "url"}
                if details["featured_image"]
                else None
            ),
            "content_images": [
                {k: v for k, v in image.items() if k != "url"}
                for image in details["content_images"]
            ],
        }
        details = BlogDetails.model_validate(details).model_dump()
    ids = {node.get("attrs", {}).get("asset_id") for node in nodes} - {None}
    cover = values.get("cover_asset_id")
    if cover is not None:
        ids.add(cover)
    ids.update(image["asset_id"] for image in details["content_images"])
    if details["featured_image"]:
        ids.add(details["featured_image"]["asset_id"])
    urls = {
        node.get("attrs", {}).get("src") for node in nodes if node["type"] in {"image", "audio"}
    }
    rows = {}
    if ids or urls:
        rows = {
            row["id"]: dict(row)
            for row in (
                await db.execute(
                    sa.select(assets).where(
                        sa.or_(assets.c.id.in_(ids), assets.c.public_url.in_(urls))
                    )
                )
            )
            .mappings()
            .all()
        }
    if ids - rows.keys():
        raise HTTPException(422, "Media asset does not exist in the selected app.")
    by_url = {}
    for row in rows.values():
        if uploader_id is not None and row["uploaded_by_membership_id"] != uploader_id:
            raise HTTPException(422, "Choose an image uploaded by your own account.")
        try:
            safe_url(row["public_url"])
            if urlsplit(row["public_url"]).query or urlsplit(row["public_url"]).fragment:
                raise ValueError()
        except ValueError:
            raise HTTPException(422, "Media needs a stable uploaded HTTPS URL.") from None
        by_url[row["public_url"]] = row
    image_rows = []
    for node in nodes:
        attrs = node.setdefault("attrs", {})
        row = (
            by_url.get(attrs.get("src"))
            if node["type"] in {"image", "audio"}
            else rows.get(attrs.get("asset_id"))
        )
        if row is None:
            if node["type"] in {"image", "audio"}:
                raise HTTPException(422, "Media URL must reference an uploaded app asset.")
            continue
        prefix = {
            "image": "image/",
            "customImage": "image/",
            "audio": "audio/",
            "customAudio": "audio/",
            "customVideo": "video/",
        }.get(node["type"])
        if prefix and not row["media_type"].startswith(prefix):
            raise HTTPException(422, "Media type does not match the document node.")
        attrs["src"] = row["public_url"]
        if node["type"] == "customFileAttachment":
            attrs.update(
                fileName=row["original_filename"],
                fileSize=f"{row['size_bytes']} bytes",
                fileType=row["media_type"],
            )
        if node in images:
            image_rows.append((node, row))
    content_ids = {row["id"] for _, row in image_rows}
    if supplied is not None:
        if content_ids != {image["asset_id"] for image in details["content_images"]}:
            raise HTTPException(422, "Content image details must match the document images.")
    else:
        # Legacy posts get safe defaults, without inventing credits or licences.
        old = {image["asset_id"]: image for image in details["content_images"]}
        details["content_images"] = [
            old.get(
                row["id"],
                ImageDetails(
                    asset_id=row["id"], alt_text=node["attrs"].get("alt", "")
                ).model_dump(),
            )
            for node, row in image_rows
            if row["id"] in content_ids
        ]
        details["content_images"] = list(
            {image["asset_id"]: image for image in details["content_images"]}.values()
        )
        if cover is None:
            details["featured_image"] = None
        elif not details["featured_image"] or details["featured_image"]["asset_id"] != cover:
            legacy = (values.get("metadata_json") or {}).get("cover_image") or {}
            details["featured_image"] = ImageDetails(
                asset_id=cover,
                alt_text=legacy.get("alt_text", ""),
                attribution=legacy.get("attribution", {}),
            ).model_dump()
    for image in [details["featured_image"], *details["content_images"]]:
        if image is None:
            continue
        row = rows[image["asset_id"]]
        if not row["media_type"].startswith("image/"):
            raise HTTPException(422, "Blog image details must reference image assets.")
        image["url"] = row["public_url"]
    by_id = {image["asset_id"]: image for image in details["content_images"]}
    for node, row in image_rows:
        node["attrs"]["alt"] = by_id[row["id"]]["alt_text"]
    values["content_json"] = document
    values["blog_details_json"] = details
    return rows[cover]["public_url"] if cover is not None else None
