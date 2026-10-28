"""Freeze actual isolated backend responses for the Next.js contract regression."""

# ruff: noqa: F811
import json
import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from main import app

from tests.test_blog_api import storage  # noqa: F401
from tests.test_dashboard import dashboard  # noqa: F401


@pytest.mark.asyncio
async def test_editor_actual_backend_contract(dashboard, storage):
    _, _, headers = dashboard
    base = "/api/v1/dashboard/blog/apps/azeemlab"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers
    ) as client:
        saved = await client.post(
            base + "/posts",
            json={
                "title": "Contract fixture",
                "category_name": "Testing",
                "content_json": {
                    "type": "doc",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "Isolated contract example."}],
                        }
                    ],
                },
            },
        )
        assert saved.status_code == 201
        posts = (await client.get(base + "/posts")).json()
        categories = (await client.get(base + "/categories")).json()
        assets = (await client.get(base + "/assets")).json()
        assert set(posts) == {"items", "total", "page", "page_size", "scope", "counts"}
        assert posts["counts"] == {"mine": 1, "total": 1}
        assert posts["scope"] == "all"
        assert set(categories) == {"items", "total", "page", "page_size"}
        assert posts["items"][0]["id"] == saved.json()["post"]["id"]
        assert categories["items"][0]["id"] == saved.json()["post"]["category_id"]
        if os.environ.get("BLOG_CONTRACT_EXPORT"):
            Path(os.environ["BLOG_CONTRACT_EXPORT"]).write_text(
                json.dumps(
                    {
                        "posts": posts,
                        "categories": categories,
                        "assets": assets,
                        "saved": saved.json(),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf8",
            )
