"""Integration tests for stat() — WebDAV inspection helper.

All paths are namespaced under "stat_tree/" to avoid polluting the shared
session-scoped WsgiDAV storage used by other integration tests.
"""

import pytest

PFX = "stat_tree/"


@pytest.fixture(autouse=True)
async def _setup_cleanup(webdav_tools):
    """Create stat_tree/ before each test, remove after."""
    await webdav_tools.mkdir(PFX)
    yield
    try:
        await webdav_tools.rm([PFX.rstrip("/")])
    except Exception:
        pass


class TestStat:
    """stat(): exists/isdir/size/modified without exceptions for missing paths."""

    @pytest.mark.asyncio
    async def test_stat_file(self, webdav_tools):
        await webdav_tools.write(PFX + "stat_file.txt", "hello")
        result = await webdav_tools.stat(PFX + "stat_file.txt")
        assert result["result"] == "True"
        d = result["data"]
        assert d["path"] == PFX + "stat_file.txt"
        assert d["exists"] is True
        assert d["isdir"] is False
        assert d["size"] == "5 B"
        assert d["modified"]

    @pytest.mark.asyncio
    async def test_stat_directory(self, webdav_tools):
        await webdav_tools.mkdir(PFX + "stat_dir")
        d = (await webdav_tools.stat(PFX + "stat_dir"))["data"]
        assert d["exists"] is True
        assert d["isdir"] is True
        # No getcontentlength for a collection: n/a rather than an empty string.
        assert d["size"] == "n/a"
        assert d["size_bytes"] is None

    @pytest.mark.asyncio
    async def test_stat_missing_returns_exists_false(self, webdav_tools):
        result = await webdav_tools.stat(PFX + "nope_missing_xyz")
        assert result["result"] == "True"
        d = result["data"]
        assert d["exists"] is False
        assert d["isdir"] is False
        assert d["size"] is None

    @pytest.mark.asyncio
    async def test_stat_empty_path_rejected(self, webdav_tools):
        result = await webdav_tools.stat("")
        assert result["result"] == "False"

    @pytest.mark.asyncio
    async def test_stat_blacklisted_denied(self, webdav_tools):
        await webdav_tools.write(PFX + "stat_bl.txt", "x")
        webdav_tools.valves.FILE_BLACKLIST = PFX + "stat_bl.txt"
        result = await webdav_tools.stat(PFX + "stat_bl.txt")
        assert result["result"] == "False"
        assert result["details"] == "Access denied"
        webdav_tools.valves.FILE_BLACKLIST = ""
