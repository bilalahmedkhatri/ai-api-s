"""Grounded deterministic metadata; canonical paths come from confirmed server settings."""

import re
from datetime import UTC
from urllib.parse import quote

from app.blog.content import plain_text


def clip(value, maximum):
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= maximum:
        return value
    return value[:maximum].rsplit(" ", 1)[0] or value[:maximum]


def iso(value):
    return value.replace(tzinfo=value.tzinfo or UTC).isoformat() if value else None


def build_metadata(target, post, cover_url=None):
    canonical = None
    if target.public_blog_url:
        canonical = (
            target.public_blog_url.replace("{locale}", quote(post["locale"], safe="-")).rstrip("/")
            + "/"
            + quote(post["slug"], safe="-")
        )
    description = clip(post["excerpt"] or plain_text(post["content_json"]), 160)
    title = clip(post["title"], 70)
    author = {"name": post["author_name"]}
    if post["author_designation"]:
        author["jobTitle"] = post["author_designation"]
    published = post["status"] == "published"
    json_ld = None
    if published and canonical:
        json_ld = {
            "@context": "https://schema.org",
            "@type": "BlogPosting",
            "headline": post["title"],
            "description": description,
            "inLanguage": post["locale"],
            "mainEntityOfPage": canonical,
            "datePublished": iso(post["published_at"]),
            "dateModified": iso(post["updated_at"]),
            "author": {"@type": "Person", **author},
        }
        if cover_url:
            json_ld["image"] = cover_url
    cover_details = (post.get("metadata_json") or {}).get("cover_image") if cover_url else None
    return {
        **({"cover_image": {**cover_details, "url": cover_url}} if cover_details else {}),
        "schema_version": 1,
        "title": title,
        "description": description,
        "canonical_url": canonical,
        "locale": post["locale"],
        "author": author,
        "author_description": post.get("author_description"),
        "robots": {"index": published, "follow": published},
        "open_graph": {
            "type": "article",
            "title": title,
            "description": description,
            "url": canonical,
            "image": cover_url,
            **({"image_alt": cover_details["alt_text"]} if cover_details else {}),
        },
        "twitter": {
            "card": "summary_large_image" if cover_url else "summary",
            "title": title,
            "description": description,
            "image": cover_url,
            **({"image_alt": cover_details["alt_text"]} if cover_details else {}),
        },
        "published_at": iso(post["published_at"]),
        "modified_at": iso(post["updated_at"]),
        "word_count": len(plain_text(post["content_json"]).split()),
        "json_ld": json_ld,
    }
