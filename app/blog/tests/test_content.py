"""Strict content and server metadata input boundaries, entirely offline."""

import pytest
from app.blog.config import BlogConfigurationError, load_registry
from app.blog.content import safe_url, slug_base, validate_document
from app.blog.contracts import PostCreate, PostUpdate
from pydantic import ValidationError


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,bad",
        "http://test.test",
        "https://u:p@test.test",
        "//evil.test",
        "https://test.test/\\bad",
        "https://test.test/\x7f",
    ],
)
def test_unsafe_links(url):
    with pytest.raises(ValueError):
        safe_url(url)


@pytest.mark.parametrize(
    "node",
    [
        {"type": "iframe"},
        {"type": []},
        {"type": "heading", "attrs": {"level": True}},
        {"type": "paragraph", "attrs": {"onclick": "evil"}},
        {"type": "text", "text": "inline at root"},
        {
            "type": "customVideo",
            "attrs": {"src": "https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ"},
        },
    ],
)
def test_unsupported_nodes(node):
    with pytest.raises(ValueError):
        validate_document({"type": "doc", "content": [node]})


def test_link_and_youtube_normalization():
    doc = validate_document(
        {
            "type": "doc",
            "content": [
                {"type": "customVideo", "attrs": {"src": "https://youtu.be/dQw4w9WgXcQ"}},
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "Link",
                            "marks": [
                                {
                                    "type": "link",
                                    "attrs": {"href": "/safe", "target": "evil", "rel": "opener"},
                                }
                            ],
                        }
                    ],
                },
            ],
        }
    )
    assert doc["content"][0]["attrs"]["src"] == "https://www.youtube.com/embed/dQw4w9WgXcQ"
    assert doc["content"][1]["content"][0]["marks"][0]["attrs"]["rel"] == "noopener noreferrer"
    assert slug_base("Hello -- WORLD!") == "hello-world"
    assert slug_base("!!!") == "post"


def test_depth_and_empty_update():
    node = {"type": "paragraph"}
    for _ in range(26):
        node = {"type": "blockquote", "content": [node]}
    with pytest.raises(ValueError):
        validate_document({"type": "doc", "content": [node]})
    with pytest.raises(ValidationError):
        PostUpdate(version=1)
    with pytest.raises(ValidationError):
        PostCreate(title="Title", content_json={"type": "doc", "content": []}, slug="injected")


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@test.test/blog",
        "https://test.test/blog?secret=1",
        "https://test.test/{unknown}/blog",
        "http://test.test/blog",
    ],
)
def test_canonical_config_rejects_untrusted_forms(url):
    with pytest.raises(BlogConfigurationError):
        load_registry({"BLOG_AZEEMLAB_PUBLIC_BLOG_URL": url})
