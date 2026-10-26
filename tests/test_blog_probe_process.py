"""Real local ffprobe plus isolated process-error/cancellation regressions; no live storage."""

# ruff: noqa: F811
import asyncio
import os
import shutil
import struct
import subprocess
import threading
import zlib

import pytest
from app.blog import media, probe_process
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from main import app

from tests.test_blog_media import b2, dashboard, send, storage  # noqa: F401

REAL_PROBE = media.probe


def png_bytes():
    def chunk(kind, data):
        return (
            struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack("!IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + chunk(b"IEND", b"")
    )


@pytest.mark.skipif(
    os.name != "nt" or not shutil.which("ffprobe"),
    reason="Windows selector-loop regression needs local ffprobe",
)
@pytest.mark.parametrize("mime", ["image/png", "image/jpeg"])
def test_real_probe_on_windows_selector_event_loop(tmp_path, mime):
    file = tmp_path / ("image.png" if mime == "image/png" else "image.jpg")
    if mime == "image/png":
        file.write_bytes(png_bytes())
    else:
        if not shutil.which("ffmpeg"):
            pytest.skip("JPEG fixture needs local ffmpeg")
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=16x16",
                "-frames:v",
                "1",
                "-y",
                str(file),
            ],
            check=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        result = runner.run(REAL_PROBE(str(file), mime))
    assert result["width"] > 0 and result["height"] > 0
    assert result["duration_seconds"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [FileNotFoundError("private path"), subprocess.TimeoutExpired("private command", 15)]
)
async def test_process_failures_are_safe_503(monkeypatch, failure):
    def fail(args):
        raise failure

    monkeypatch.setattr(probe_process, "_run", fail)
    with pytest.raises(HTTPException) as error:
        await REAL_PROBE("unused.png", "image/png")
    assert error.value.status_code == 503
    assert "private" not in error.value.detail


@pytest.mark.asyncio
async def test_cancel_waits_for_worker_before_caller_cleanup(monkeypatch):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def worker(args):
        started.set()
        release.wait(2)
        finished.set()
        return 0, b"{}"

    monkeypatch.setattr(probe_process, "_run", worker)
    task = asyncio.create_task(probe_process.probe_output(["ffprobe"]))
    assert await asyncio.to_thread(started.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


@pytest.mark.asyncio
async def test_probe_output_is_bounded(monkeypatch):
    def run(args, **kwargs):
        kwargs["stdout"].write(b"x" * 100000)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(probe_process.subprocess, "run", run)
    code, output = await probe_process.probe_output(["ffprobe"])
    assert code == 0 and len(output) == 65537


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which("ffprobe"), reason="Requires local ffprobe")
async def test_upload_uses_real_probe_then_fake_storage(dashboard, b2, monkeypatch):
    _, member, headers = dashboard
    monkeypatch.setattr(media, "probe", REAL_PROBE)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await send(client, headers, content=png_bytes())
    assert response.status_code == 201, response.text
    assert response.json()["asset"]["uploaded_by_membership_id"] == member.id
    assert response.json()["asset"]["width"] == 1
    assert b2.puts == 1
