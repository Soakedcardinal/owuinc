"""Integration tests for WebDAV file operations — edge cases and agent scenarios.

All paths are namespaced under "edge/" to avoid polluting the shared session-scoped
WsgiDAV storage used by other integration tests.
"""

import pytest

PFX = "edge/"


@pytest.fixture(autouse=True)
async def _setup_cleanup(webdav_tools):
    """Create edge/ before each test, remove after."""
    await webdav_tools.mkdir(PFX)
    yield
    try:
        await webdav_tools.rm([PFX.rstrip("/")])
    except Exception:
        pass


class TestGrepEdgeCases:
    """Test grep with invalid regex and edge cases."""

    @pytest.mark.asyncio
    async def test_grep_invalid_regex_returns_error(self, webdav_tools):
        await webdav_tools.write_file(PFX + "target.txt", "test content here")

        result = await webdav_tools.grep("[invalid(regex", include="*.txt", path=PFX)
        assert result["result"] == "False"
        assert "details" in result

    @pytest.mark.asyncio
    async def test_grep_invalid_regex_unclosed_bracket(self, webdav_tools):
        await webdav_tools.write_file(PFX + "target2.txt", "some text")

        result = await webdav_tools.grep("(?P<name", include="*.txt", path=PFX)
        assert result["result"] == "False"

    @pytest.mark.asyncio
    async def test_grep_scoped_to_subdir(self, webdav_tools):
        await webdav_tools.mkdir(PFX + "scope")
        await webdav_tools.write_file(PFX + "scope/inner.txt", "match this line")
        await webdav_tools.write_file(PFX + "outside.txt", "match this line")

        result = await webdav_tools.grep("match this", path=PFX + "scope")
        assert result["result"] == "True"
        assert len(result["data"]) == 1
        assert "scope/inner.txt" in result["data"][0]["file"]

    @pytest.mark.asyncio
    async def test_grep_scoped_nonexistent_dir(self, webdav_tools):
        result = await webdav_tools.grep("anything", path=PFX + "no_such_dir")
        assert result["result"] == "False"


class TestGlobBraceExpansion:
    """Test glob brace expansion {*.py,*.js} pattern."""

    @pytest.mark.asyncio
    async def test_glob_brace_expansion_basic(self, webdav_tools):
        await webdav_tools.write_file(PFX + "a.py", "python")
        await webdav_tools.write_file(PFX + "b.js", "javascript")
        await webdav_tools.write_file(PFX + "c.txt", "text")

        result = await webdav_tools.glob("{*.py,*.js}", path=PFX)
        assert result["result"] == "True"
        files = result["data"]
        assert len(files) == 2
        names = [f.split("/")[-1] for f in files]
        assert "a.py" in names
        assert "b.js" in names
        assert not any("c.txt" in f for f in files)

    @pytest.mark.asyncio
    async def test_glob_brace_expansion_with_prefix(self, webdav_tools):
        await webdav_tools.write_file(PFX + "lib_a.py", "a")
        await webdav_tools.write_file(PFX + "lib_b.js", "b")
        await webdav_tools.write_file(PFX + "lib_c.txt", "c")

        result = await webdav_tools.glob("lib_{*.py,*.js}", path=PFX)
        assert result["result"] == "True"
        files = result["data"]
        assert len(files) == 2


class TestLsEdgeCases:
    """Test ls on nonexistent directories and on files."""

    @pytest.mark.asyncio
    async def test_ls_nonexistent_directory(self, webdav_tools):
        result = await webdav_tools.ls(PFX + "no_such_directory_xyz")
        assert result["result"] == "False"

    @pytest.mark.asyncio
    async def test_ls_on_file_not_directory(self, webdav_tools):
        """ls on a file returns success (WsgiDAV treats file as a listing target,
        returning the file itself as a single entry). This is acceptable behavior."""
        await webdav_tools.write_file(PFX + "ls_on_me.txt", "content here")

        result = await webdav_tools.ls(PFX + "ls_on_me.txt")
        assert result["result"] == "True"


class TestRmEdgeCases:
    """Test rm edge cases: empty list, partial failure."""

    @pytest.mark.asyncio
    async def test_rm_empty_list_succeeds_noop(self, webdav_tools):
        result = await webdav_tools.rm([])
        assert result["result"] == "True"

    @pytest.mark.asyncio
    async def test_rm_nonexistent_path_fails(self, webdav_tools):
        result = await webdav_tools.rm([PFX + "nonexistent_xyz_file.txt"])
        assert result["result"] == "False"

    @pytest.mark.asyncio
    async def test_rm_mixed_existing_and_nonexistent(self, webdav_tools):
        await webdav_tools.write_file(PFX + "partial_ok.txt", "will be deleted")

        result = await webdav_tools.rm(
            [PFX + "partial_ok.txt", PFX + "nonexistent.txt"]
        )
        assert result["result"] == "False"

        remaining = await webdav_tools.read_file(PFX + "partial_ok.txt")
        assert remaining["result"] == "False"


class TestReadEdgeCases2:
    """Additional read_file edge cases."""

    @pytest.mark.asyncio
    async def test_read_file_empty_path_rejected(self, webdav_tools):
        result = await webdav_tools.read_file("")
        assert result["result"] == "False"

    @pytest.mark.asyncio
    async def test_read_negative_offset_rejected(self, webdav_tools):
        await webdav_tools.write_file(PFX + "neg_offset.txt", "line1\nline2\nline3")

        result = await webdav_tools.read_file(PFX + "neg_offset.txt", offset=-1)
        assert result["result"] == "False"

    @pytest.mark.asyncio
    async def test_read_negative_limit_rejected(self, webdav_tools):
        await webdav_tools.write_file(PFX + "neg_limit.txt", "line1\nline2\nline3")

        result = await webdav_tools.read_file(PFX + "neg_limit.txt", limit=-5)
        assert result["result"] == "False"

    @pytest.mark.asyncio
    async def test_read_whitespace_only_path(self, webdav_tools):
        """Whitespace-only path resolves to sandbox root directory.
        WebDAV server returns HTML directory listing content (not an error).
        This is expected — the tool can't distinguish directory from file
        before attempting the read."""
        result = await webdav_tools.read_file("   ")
        assert result["result"] == "True"
        assert "WsgiDAV" in result["data"] or "<html" in result["data"]


class TestWriteParentIsFile:
    """Test write_file where parent path is an existing file."""

    @pytest.mark.asyncio
    async def test_write_file_parent_is_file_fails(self, webdav_tools):
        await webdav_tools.write_file(PFX + "parent_is_file.txt", "original")

        result = await webdav_tools.write_file(
            PFX + "parent_is_file.txt/child.txt", "data"
        )
        assert result["result"] == "False"


class TestEditMultiline:
    """Test edit with multiline old_string."""

    @pytest.mark.asyncio
    async def test_edit_multiline_old_string(self, webdav_tools):
        content = "line1\nline2\nline3\nline4"
        await webdav_tools.write_file(PFX + "multi_edit.txt", content)

        result = await webdav_tools.edit(
            PFX + "multi_edit.txt", "line2\nline3", "REPLACED"
        )
        assert result["result"] == "True"

        readback = await webdav_tools.read_file(PFX + "multi_edit.txt")
        assert readback["data"] == "line1\nREPLACED\nline4"

    @pytest.mark.asyncio
    async def test_edit_multiline_not_found(self, webdav_tools):
        await webdav_tools.write_file(PFX + "multi_edit2.txt", "line1\nline2\nline3")

        result = await webdav_tools.edit(
            PFX + "multi_edit2.txt", "line1\nline3", "NOPE"
        )
        assert result["result"] == "False"


class TestBlacklistErrorSanitization:
    """Verify blacklist errors don't leak path information."""

    @pytest.mark.asyncio
    async def test_blacklisted_error_no_path_leak(self, webdav_tools):
        webdav_tools.valves.FILE_BLACKLIST = PFX + "secret_dir"
        await webdav_tools.mkdir(PFX + "secret_dir")
        try:
            result = await webdav_tools.read_file(PFX + "secret_dir/file.txt")
            assert result["result"] == "False"
            assert "secret_dir" not in result.get("details", "")
            assert "file.txt" not in result.get("details", "")
        finally:
            webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_blacklisted_error_no_path_leak_on_write(self, webdav_tools):
        webdav_tools.valves.FILE_BLACKLIST = PFX + "hidden"
        try:
            result = await webdav_tools.write_file(PFX + "hidden/evil.txt", "bad")
            assert result["result"] == "False"
            assert "hidden" not in result.get("details", "")
            assert "evil.txt" not in result.get("details", "")
        finally:
            webdav_tools.valves.FILE_BLACKLIST = ""


class TestMvCpToExistingDirectory:
    """Test mv/cp when destination is an existing directory."""

    @pytest.mark.asyncio
    async def test_mv_file_into_existing_directory(self, webdav_tools):
        await webdav_tools.mkdir(PFX + "target_dir")
        await webdav_tools.write_file(PFX + "into_dir.txt", "moved content")

        result = await webdav_tools.mv(
            PFX + "into_dir.txt", PFX + "target_dir/into_dir.txt"
        )
        assert result["result"] == "True"

        readback = await webdav_tools.read_file(PFX + "target_dir/into_dir.txt")
        assert readback["data"] == "moved content"

        gone = await webdav_tools.read_file(PFX + "into_dir.txt")
        assert gone["result"] == "False"

    @pytest.mark.asyncio
    async def test_cp_file_into_existing_directory(self, webdav_tools):
        await webdav_tools.mkdir(PFX + "target_dir")
        await webdav_tools.write_file(PFX + "into_dir.txt", "copied content")

        result = await webdav_tools.cp(
            PFX + "into_dir.txt", PFX + "target_dir/into_dir.txt"
        )
        assert result["result"] == "True"

        readback = await webdav_tools.read_file(PFX + "target_dir/into_dir.txt")
        assert readback["data"] == "copied content"

        original = await webdav_tools.read_file(PFX + "into_dir.txt")
        assert original["data"] == "copied content"


class TestAppendOverwriteInteraction:
    """Test append_file followed by read_file and edge cases."""

    @pytest.mark.asyncio
    async def test_append_file_no_trailing_newline(self, webdav_tools):
        await webdav_tools.write_file(PFX + "append_nl.txt", "line1")
        await webdav_tools.append_file(PFX + "append_nl.txt", "line2")

        readback = await webdav_tools.read_file(PFX + "append_nl.txt")
        assert readback["data"] == "line1line2"

    @pytest.mark.asyncio
    async def test_append_file_with_explicit_newline(self, webdav_tools):
        await webdav_tools.write_file(PFX + "append_nl2.txt", "line1\n")
        await webdav_tools.append_file(PFX + "append_nl2.txt", "line2")

        readback = await webdav_tools.read_file(PFX + "append_nl2.txt")
        assert readback["data"] == "line1\nline2"


class TestGlobWithDetailMode:
    """Verify glob behavior in nested structures with blacklisted dirs."""

    @pytest.mark.asyncio
    async def test_glob_nested_with_blacklist_filters(self, webdav_tools):
        webdav_tools.valves.FILE_BLACKLIST = PFX + "blocked"
        try:
            await webdav_tools.mkdir(PFX + "blocked")
            await webdav_tools.mkdir(PFX + "open")
            await webdav_tools.write_file(PFX + "blocked/secret.py", "x")
            await webdav_tools.write_file(PFX + "open/public.py", "y")

            result = await webdav_tools.glob("*.py", path=PFX)
            assert result["result"] == "True"
            assert not any("blocked" in f for f in result["data"])
            assert any("open/public.py" in f for f in result["data"])
        finally:
            webdav_tools.valves.FILE_BLACKLIST = ""


class TestLargeOperations:
    """Test operations with moderate-size content to catch encoding issues."""

    @pytest.mark.asyncio
    async def test_write_read_10000_lines(self, webdav_tools):
        lines = [f"line_{i}_data_padding" for i in range(10000)]
        content = "\n".join(lines)
        await webdav_tools.write_file(PFX + "large_file.txt", content)

        readback = await webdav_tools.read_file(PFX + "large_file.txt")
        assert readback["result"] == "True"
        assert readback["data"] == content

    @pytest.mark.asyncio
    async def test_write_read_unicode_content(self, webdav_tools):
        content = "Hello \u4e16\u754c \U0001f30f \u00e9\u00e8\u00ea"
        await webdav_tools.write_file(PFX + "unicode.txt", content)

        readback = await webdav_tools.read_file(PFX + "unicode.txt")
        assert readback["result"] == "True"
        assert readback["data"] == content
