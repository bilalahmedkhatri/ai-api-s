"""Real HTTP from the executor into the real gateway, with isolated SQL only."""

# ruff: noqa: F811
import asyncio
import base64
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import AsyncMock

from app.models.dashboard import ServiceCredential
from httpx import ASGITransport, AsyncClient
from main import app
from sqlalchemy import select

from tests.test_customer_api_keys import customer
from tests.test_dashboard import dashboard  # noqa: F401


async def test_default_credential_execution_over_real_http(dashboard, monkeypatch):
    db, _, _ = dashboard
    m, headers = await customer(db)
    monkeypatch.setattr(
        "app.api.v1.audio.router.get_dynamic_tts_models", AsyncMock(return_value=[])
    )
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            async def forward():
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://isolated-gateway"
                ) as client:
                    return await client.get(
                        self.path, headers={"X-API-Key": self.headers.get("X-API-Key", "")}
                    )

            response = asyncio.run(forward())
            seen.append((self.path, response.status_code))
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(response.content)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("DASHBOARD_INTERNAL_URL", f"http://127.0.0.1:{server.server_port}")
    try:
        identifier = base64.urlsafe_b64encode(b"get:/api/v1/audio/models").decode().rstrip("=")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for _ in range(2):
                response = await client.post(
                    f"/api/v1/dashboard/operations/{identifier}",
                    headers=headers,
                    data={"credential": ""},
                )
                assert response.status_code == 200 and response.json()["status"] == 200, (
                    response.text
                )
                assert "gw_live_" not in response.text
        assert seen == [("/api/v1/audio/models", 200)] * 2
        managed = (
            (
                await db.execute(
                    select(ServiceCredential).where(ServiceCredential.membership_id == m.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(managed) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
