"""Bounded Tiptap JSON validation; no HTML execution, fetching or client-owned media."""

import copy
import json
import re
import unicodedata
from urllib.parse import parse_qs, urlsplit

from app.blog.table_content import TABLE_ATTRIBUTES, TABLE_NODES, validate_table_node

MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_TEXT = 200_000
MEDIA_TYPES = {"customImage", "customVideo", "customFileAttachment", "customAudio", "image", "audio"}
CONTAINERS = {
    "doc",
    "paragraph",
    "heading",
    "bulletList",
    "orderedList",
    "listItem",
    "blockquote",
    "codeBlock",
}
CONTAINERS |= TABLE_NODES
LEAVES = {"text", "hardBreak", "horizontalRule", "youtube"} | MEDIA_TYPES


def clean_text(value, limit, *, empty=False):
    if not isinstance(value, str):
        raise ValueError("Expected plain text.")
    value = unicodedata.normalize("NFC", value).strip()
    if len(value) > limit or (not empty and not value):
        raise ValueError("Text length is invalid.")
    if any(unicodedata.category(char) in {"Cc", "Cs"} for char in value):
        raise ValueError("Control characters are not allowed.")
    return value


def safe_url(value, *, relative=False):
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or any(char.isspace() or unicodedata.category(char) in {"Cc", "Cs"} for char in value)
        or "\\" in value
    ):
        raise ValueError("Invalid URL.")
    if relative and (
        (value.startswith("/") and not value.startswith("//")) or value.startswith("#")
    ):
        return value
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Use a credential-free HTTPS URL.")
    _ = parsed.port
    return value


def youtube_url(value):
    parsed = urlsplit(safe_url(value))
    if parsed.port not in {None, 443}:
        raise ValueError("Unsupported video URL.")
    host = parsed.hostname.lower()
    if host == "youtu.be":
        identifier = parsed.path.strip("/")
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            identifier = parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith("/embed/"):
            identifier = parsed.path.removeprefix("/embed/")
        else:
            identifier = ""
    else:
        identifier = ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", identifier):
        raise ValueError("Use a supported YouTube video URL or an uploaded video asset.")
    return "https://www.youtube.com/embed/" + identifier


def validate_document(value):
    if not isinstance(value, dict):
        raise ValueError("Content must be a Tiptap document.")
    try:
        if (
            len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            > MAX_DOCUMENT_BYTES
        ):
            raise ValueError("Document is too large.")
    except (RecursionError, UnicodeError, TypeError):
        raise ValueError("Invalid document encoding.") from None
    result = copy.deepcopy(value)
    stack = [(result, 0, None)]
    nodes = chars = 0
    while stack:
        node, depth, parent = stack.pop()
        nodes += 1
        if nodes > 8000 or depth > 24 or not isinstance(node, dict):
            raise ValueError("Document structure exceeds supported limits.")
        if set(node) - {"type", "attrs", "content", "text", "marks"}:
            raise ValueError("Unsupported document field.")
        kind = node.get("type")
        if (
            not isinstance(kind, str)
            or kind not in CONTAINERS | LEAVES
            or (depth == 0 and kind != "doc")
            or (depth > 0 and kind == "doc")
        ):
            raise ValueError("Unsupported document node.")
        attrs = node.get("attrs", {})
        if not isinstance(attrs, dict):
            raise ValueError("Invalid node attributes.")
        allowed = {
            **TABLE_ATTRIBUTES,
            "heading": {"level"},
            "orderedList": {"start", "type"},
            "codeBlock": {"language"},
        }
        if kind in {"image", "audio", "youtube"}:
            allowed[kind] = {
                "image": {"src", "alt", "title", "width", "height"},
                "audio": {"src", "controls", "autoplay", "loop", "muted", "preload", "controlslist", "crossorigin", "disableremoteplayback"},
                "youtube": {"src", "width", "height", "start"},
            }[kind]
            attrs["src"] = youtube_url(attrs.get("src")) if kind == "youtube" else safe_url(attrs.get("src"))
            for key in ("width", "height", "start"):
                value = attrs.get(key)
                if value is not None and (type(value) is not int or not (0 if key == "start" else 1) <= value <= (86400 if key == "start" else 100000)):
                    raise ValueError("Invalid media dimensions or start time.")
            for key in ("alt", "title"):
                if attrs.get(key) is not None:
                    clean_text(attrs[key], 1000, empty=True)
            if kind == "audio":
                for key in ("controls", "autoplay", "loop", "muted", "disableremoteplayback"):
                    if attrs.get(key) is not None and type(attrs[key]) is not bool:
                        raise ValueError("Invalid audio option.")
                if attrs.get("preload") is not None and attrs["preload"] not in {"none", "metadata", "auto"}:
                    raise ValueError("Invalid audio preload.")
                if attrs.get("crossorigin") is not None and attrs["crossorigin"] not in {"", "anonymous", "use-credentials"}:
                    raise ValueError("Invalid audio CORS option.")
                if attrs.get("controlslist") is not None:
                    clean_text(attrs["controlslist"], 100, empty=True)
        elif kind in MEDIA_TYPES:
            allowed[kind] = {
                "asset_id",
                "src",
                "alt",
                "title",
                "caption",
                "fileName",
                "fileSize",
                "fileType",
            }
            asset_id = attrs.get("asset_id")
            if asset_id is not None and (
                type(asset_id) is not int or not 0 < asset_id <= 2147483647
            ):
                raise ValueError("Invalid asset reference.")
            if asset_id is None:
                if kind != "customVideo":
                    raise ValueError("Upload media and reference its asset ID.")
                attrs["src"] = youtube_url(attrs.get("src"))
            elif attrs.get("src") is not None:
                safe_url(attrs["src"])
            for key, item in attrs.items():
                if key not in {"asset_id", "src"}:
                    clean_text(item, 1000, empty=True)
        if set(attrs) - allowed.get(kind, set()):
            raise ValueError("Unsupported node attributes.")
        if kind == "heading" and (
            type(attrs.get("level")) is not int or attrs["level"] not in range(1, 7)
        ):
            raise ValueError("Invalid heading level.")
        if (
            kind == "orderedList"
            and "start" in attrs
            and (type(attrs["start"]) is not int or not 1 <= attrs["start"] <= 100000)
        ):
            raise ValueError("Invalid ordered-list start.")
        if kind == "codeBlock" and attrs.get("language") is not None:
            clean_text(attrs["language"], 40, empty=True)
        if kind == "orderedList" and (
            attrs.get("type") is not None
            and (
                not isinstance(attrs["type"], str) or attrs["type"] not in {"1", "a", "A", "i", "I"}
            )
        ):
            raise ValueError("Invalid ordered-list type.")
        if kind == "text":
            text = node.get("text")
            if not isinstance(text, str) or any(
                unicodedata.category(char) == "Cs"
                or (unicodedata.category(char) == "Cc" and char not in "\n\t")
                for char in text
            ):
                raise ValueError("Invalid document text.")
            chars += len(text)
        elif "text" in node:
            raise ValueError("Only text nodes may contain text.")
        if chars > MAX_TEXT:
            raise ValueError("Document text is too long.")
        marks = node.get("marks", [])
        if not isinstance(marks, list) or len(marks) > 8 or (marks and kind != "text"):
            raise ValueError("Invalid text marks.")
        for mark in marks:
            if (
                not isinstance(mark, dict)
                or set(mark) - {"type", "attrs"}
                or not isinstance(mark.get("type"), str)
                or mark.get("type") not in {"bold", "italic", "strike", "code", "link", "underline"}
            ):
                raise ValueError("Unsupported text mark.")
            mark_attrs = mark.get("attrs", {})
            if not isinstance(mark_attrs, dict):
                raise ValueError("Invalid mark attributes.")
            if mark["type"] == "link":
                if set(mark_attrs) - {"href", "target", "rel", "class", "title"}:
                    raise ValueError("Unsupported link attributes.")
                href = safe_url(mark_attrs.get("href"), relative=True)
                mark["attrs"] = {"href": href, "target": "_blank", "rel": "noopener noreferrer"}
                if mark_attrs.get("title") is not None:
                    mark["attrs"]["title"] = clean_text(mark_attrs["title"], 1000, empty=True)
            elif mark_attrs:
                raise ValueError("Unsupported mark attributes.")
        children = node.get("content", [])
        if not isinstance(children, list) or (children and kind in LEAVES):
            raise ValueError("Invalid node children.")
        validate_table_node(kind, attrs, parent, children)
        if kind == "doc" and "content" not in node:
            raise ValueError("Document content is required.")
        if parent in {"paragraph", "heading"} and kind not in {"text", "hardBreak"}:
            raise ValueError("Block nodes cannot occur inside inline text.")
        if parent == "codeBlock" and kind != "text":
            raise ValueError("Code blocks contain text only.")
        if parent in {"bulletList", "orderedList"} and kind != "listItem":
            raise ValueError("Lists contain list items only.")
        if parent in {"doc", "blockquote", "listItem"} and kind in {
            "text",
            "hardBreak",
            "listItem",
        }:
            raise ValueError("Invalid block structure.")
        stack.extend((child, depth + 1, kind) for child in reversed(children))
    return result


def document_nodes(document):
    stack = [document]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.get("content", [])))


def plain_text(document):
    return " ".join(node["text"] for node in document_nodes(document) if node["type"] == "text")


def slug_base(text, limit=180):
    normalized = unicodedata.normalize("NFKC", text).casefold()
    value = "".join(
        char if char.isalnum() or unicodedata.category(char).startswith("M") else "-"
        for char in normalized
    )
    return re.sub("-+", "-", value).strip("-")[:limit].rstrip("-") or "post"
