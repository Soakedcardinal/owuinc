"""Connection lifecycle guard: no fd/connection growth across tool calls.

aiowebdav2 discards successful responses it never reads, and aiohttp logs
"Unclosed connection" ResourceWarnings when the connection wrappers are
garbage-collected after the session is already closed (aio-libs/aiohttp#5277:
GC timing, not a leak — the transport is closed by the time the warning fires).
This test asserts the functional property that actually matters for a
long-running OpenWebUI process: repeated tool calls, each with its own
client/session, must not accumulate file descriptors or connections.
"""

import asyncio
import gc
import os

import pytest


def _fd_count():
    return len(os.listdir("/proc/self/fd"))


@pytest.mark.asyncio
async def test_no_fd_growth_across_tool_calls(webdav_tools):
    before = _fd_count()
    for i in range(40):
        await webdav_tools.write(f"fdcheck/{i}.txt", "x" * 100)
        await webdav_tools.cat(f"fdcheck/{i}.txt")
        await webdav_tools.rm([f"fdcheck/{i}.txt"])
    await asyncio.sleep(0.5)
    gc.collect()
    after = _fd_count()
    assert after <= before + 3, f"fd growth: {before} -> {after}"
