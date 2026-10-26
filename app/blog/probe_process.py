"""Bounded ffprobe subprocess I/O, including Windows selector event loops."""

import asyncio
import os
import subprocess
import tempfile
from contextlib import suppress


def _run(arguments):
    # A file bounds memory even if a malformed input produces excessive probe output.
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        output.seek(0)
        return result.returncode, output.read(65537)


async def probe_output(arguments):
    # subprocess.run kills/waits on timeout. Drain it on cancellation before the
    # caller removes the upload file and releases its bounded upload slot.
    task = asyncio.create_task(asyncio.to_thread(_run, arguments))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(Exception):
            await task
        raise
