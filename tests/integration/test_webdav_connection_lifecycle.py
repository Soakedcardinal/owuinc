"""Connection lifecycle guards: no socket/fd accumulation in long-running use.

aiowebdav2 discards successful responses it never reads, and aiohttp logs
"Unclosed connection" ResourceWarnings when the connection wrappers are
garbage-collected after the session is already closed (aio-libs/aiohttp#5277:
GC timing, not a leak — the transport is closed by the time the warning
fires). These tests assert the functional properties that actually matter
for a long-running OpenWebUI process:

- repeated tool calls (each with its own client/session) transfer real
  content and do not accumulate file descriptors;
- sequential read_from() calls on one client (the pattern grep() uses for
  every file) release each response back to the pool deterministically,
  instead of holding one socket per file;
- a full grep over a populated tree keeps the fd count bounded by the
  fetch concurrency, not by the file count.

Every operation result and payload is asserted, so the tests cannot
silently degenerate into no-ops (a 404'd write still exercises a client
lifecycle but transfers nothing).
"""

import asyncio
import gc
import os
from io import BytesIO

import pytest

from owuinc.owuinc import _webdav_path


def _fd_count():
    return len(os.listdir("/proc/self/fd"))


@pytest.mark.asyncio
async def test_no_fd_growth_across_tool_calls(webdav_tools):
    await webdav_tools.mkdir("fdcheck")
    before = _fd_count()
    try:
        for i in range(40):
            w = await webdav_tools.write(f"fdcheck/{i}.txt", "x" * 100)
            assert w["result"] == "True", f"write {i}: {w}"
            c = await webdav_tools.cat(f"fdcheck/{i}.txt")
            assert c["result"] == "True", f"cat {i}: {c}"
            assert c["data"] == "x" * 100, f"content round-trip failed on {i}"
            r = await webdav_tools.rm([f"fdcheck/{i}.txt"])
            assert r["result"] == "True", f"rm {i}: {r}"
        await asyncio.sleep(0.5)
        gc.collect()
        after = _fd_count()
        assert after <= before + 3, f"fd growth: {before} -> {after}"
    finally:
        await webdav_tools.rm(["fdcheck"])


@pytest.mark.asyncio
async def test_read_from_releases_connection_within_session(webdav_tools):
    """One client, 25 sequential read_from() calls: the connector must have
    no checked-out connections after each call (per-response release), and
    the fd count must stay flat (connection reuse, one socket per file
    would grow it toward the pool limit)."""
    await webdav_tools.mkdir("reuse")
    client = webdav_tools._webdav_client()
    try:
        for i in range(5):
            w = await webdav_tools.write(f"reuse/{i}.txt", f"content {i}")
            assert w["result"] == "True", f"seed write {i}: {w}"
        baseline = _fd_count()
        peak = baseline
        for i in range(25):
            buf = BytesIO()
            await client.resource(
                _webdav_path(f"{webdav_tools.valves.SANDBOX_DIR}/reuse/{i % 5}.txt")
            ).read_from(buf)
            assert buf.getvalue() == f"content {i % 5}".encode(), f"read {i} corrupted"
            # A held (unreleased) response keeps its connection checked out.
            assert (
                len(client._session.connector._acquired) == 0
            ), f"response not released after read_from {i}"
            peak = max(peak, _fd_count())
        assert (
            peak <= baseline + 2
        ), f"no connection reuse: peak fds {peak} vs {baseline}"
    finally:
        await client.close()
        await webdav_tools.rm(["reuse"])


@pytest.mark.asyncio
async def test_grep_under_load_keeps_fds_bounded(webdav_tools):
    """grep() fetches every listed file (concurrency-capped); with proper
    per-response release the fd count is bounded by the cap, not the file
    count. A leak would open one socket per file up to the pool limit."""
    n_files = 15
    await webdav_tools.mkdir("gload")
    try:
        for i in range(n_files):
            w = await webdav_tools.write(f"gload/{i:02d}.txt", f"gload line {i}")
            assert w["result"] == "True", f"seed write {i}: {w}"
        before = _fd_count()
        peak = before
        stop = False

        async def _sampler():
            nonlocal peak
            while not stop:
                peak = max(peak, _fd_count())
                await asyncio.sleep(0.02)

        sampler = asyncio.create_task(_sampler())
        try:
            res = await webdav_tools.grep("gload line", path="gload")
        finally:
            stop = True
            await sampler
        assert res["result"] == "True", f"grep: {res}"
        assert len(res["data"]["matches"]) == n_files, "every file should match"
        assert peak <= before + 8, f"fd spike under grep load: {before} -> {peak}"
    finally:
        await webdav_tools.rm(["gload"])
