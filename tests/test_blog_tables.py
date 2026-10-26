"""Table save/reload using the existing isolated blog API fixtures; no live database."""

# ruff: noqa: F811

import pytest
from app.blog.tests.test_table_content import table_document
from httpx import ASGITransport, AsyncClient
from main import app

from tests.test_blog_api import BASE, create, storage  # noqa: F401
from tests.test_dashboard import dashboard  # noqa: F401


@pytest.mark.asyncio
async def test_table_create_update_and_reload(dashboard, storage):
    _, _, headers = dashboard
    document = table_document()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await create(client, headers, content_json=document)
        assert response.status_code == 201, response.text
        saved = response.json()["post"]
        assert saved["content_json"] == document
        path = BASE + f"/apps/azeemlab/posts/{saved['id']}"
        document["content"][0]["content"][0]["content"][0]["attrs"] = {
            "colspan": 1,
            "rowspan": 1,
            "colwidth": None,
            "align": "center",
        }
        response = await client.patch(
            path, headers=headers, json={"version": saved["version"], "content_json": document}
        )
        assert response.status_code == 200, response.text
        loaded = (await client.get(path, headers=headers)).json()["post"]
        assert loaded["content_json"] == document
        invalid = {"type": "doc", "content": [{"type": "tableRow"}]}
        rejected = await client.patch(
            path, headers=headers, json={"version": loaded["version"], "content_json": invalid}
        )
        assert rejected.status_code == 422
        assert (await client.get(path, headers=headers)).json()["post"]["version"] == loaded[
            "version"
        ]
