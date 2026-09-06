"""Integration tests for stat() and tree() — WebDAV inspection helpers.

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


class TestTree:
    """tree(): indented recursive listing with depth limit and blacklist filtering."""

    @pytest.mark.asyncio
    async def test_tree_structure(self, webdav_tools):
        await webdav_tools.mkdir(PFX + "tree_root/sub")
        await webdav_tools.write(PFX + "tree_root/a.txt", "a")
        await webdav_tools.write(PFX + "tree_root/sub/b.md", "b")
        lines = (await webdav_tools.tree(PFX + "tree_root"))["data"]
        assert "a.txt" in lines
        assert "sub/" in lines
        assert "  b.md" in lines
        assert not any("tree_root" in ln for ln in lines)

    @pytest.mark.asyncio
    async def test_tree_depth_limit(self, webdav_tools):
        await webdav_tools.mkdir(PFX + "tree_d/a/b")
        await webdav_tools.write(PFX + "tree_d/a/b/deep.txt", "x")
        lines = (await webdav_tools.tree(PFX + "tree_d", depth=1))["data"]
        assert "a/" in lines
        assert not any("deep.txt" in ln for ln in lines)
        assert not any(ln.rstrip().endswith("b/") for ln in lines)

    @pytest.mark.asyncio
    async def test_tree_depth_clamped(self, webdav_tools):
        await webdav_tools.mkdir(PFX + "tree_c/d1")
        await webdav_tools.write(PFX + "tree_c/d1/x.txt", "x")
        result = await webdav_tools.tree(PFX + "tree_c", depth=99)
        assert result["result"] == "True"
        assert result["data"] == ["d1/", "  x.txt"]

    @pytest.mark.asyncio
    async def test_tree_blacklisted_hidden(self, webdav_tools):
        await webdav_tools.mkdir(PFX + "tree_bl/hidden")
        await webdav_tools.write(PFX + "tree_bl/hidden/s.txt", "x")
        await webdav_tools.mkdir(PFX + "tree_bl/ok")
        webdav_tools.valves.FILE_BLACKLIST = PFX + "tree_bl/hidden"
        lines = (await webdav_tools.tree(PFX + "tree_bl"))["data"]
        assert any("ok/" in ln for ln in lines)
        assert not any("hidden" in ln or "s.txt" in ln for ln in lines)
        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_tree_empty_dir(self, webdav_tools):
        await webdav_tools.mkdir(PFX + "tree_empty")
        lines = (await webdav_tools.tree(PFX + "tree_empty"))["data"]
        assert lines == []
