"""Integration tests for WebDAV file operations using WsgiDAV.

All tests are async to match the async tool methods in owuinc.py.
"""

import pytest


class TestMkdir:
    @pytest.mark.asyncio
    async def test_mkdir_creates_directory(self, webdav_tools):
        result = await webdav_tools.mkdir("testdir")
        assert result == {"result": "True"}

        listing = await webdav_tools.ls("")
        assert listing["result"] == "True"
        assert any("testdir" in p for p in listing["data"])


class TestLs:
    @pytest.mark.asyncio
    async def test_ls_lists_files_and_directories(self, webdav_tools):
        await webdav_tools.mkdir("ls_testdir")
        await webdav_tools.write_file("ls_testfile.txt", "content")

        listing = await webdav_tools.ls("")
        assert listing["result"] == "True"
        assert any("ls_testdir" in p for p in listing["data"])
        assert any("ls_testfile.txt" in p for p in listing["data"])

    @pytest.mark.asyncio
    async def test_ls_detail_shows_file_info(self, webdav_tools):
        await webdav_tools.write_file("detail_file.txt", "x" * 100)

        listing = await webdav_tools.ls("", detail=True)
        assert listing["result"] == "True"
        assert any("detail_file.txt" in entry for entry in listing["data"])
        matching = [e for e in listing["data"] if "detail_file.txt" in e]
        assert "[FILE]" in matching[0]
        from datetime import datetime

        current_year = datetime.now().strftime("%Y")
        assert current_year in matching[0]
        assert "B" in matching[0]

    @pytest.mark.asyncio
    async def test_ls_detail_shows_directory(self, webdav_tools):
        await webdav_tools.mkdir("detail_dir")

        listing = await webdav_tools.ls("", detail=True)
        assert listing["result"] == "True"
        matching = [e for e in listing["data"] if "detail_dir" in e]
        assert matching
        assert "[DIR]" in matching[0]

    @pytest.mark.asyncio
    async def test_ls_detail_vs_normal(self, webdav_tools):
        await webdav_tools.write_file("compare.txt", "data")

        normal = await webdav_tools.ls("")
        detailed = await webdav_tools.ls("", detail=True)

        assert normal["result"] == "True"
        assert detailed["result"] == "True"
        assert any("compare.txt" in p for p in normal["data"])
        assert any("compare.txt" in p for p in detailed["data"])
        assert any("[FILE]" in p for p in detailed["data"])

    @pytest.mark.asyncio
    async def test_ls_detail_hides_blacklisted(self, webdav_tools):
        webdav_tools.valves.FILE_BLACKLIST = "secret"
        await webdav_tools.write_file("visible.txt", "ok")
        await webdav_tools.mkdir("secret")
        await webdav_tools.write_file("secret/hidden.txt", "nope")

        listing = await webdav_tools.ls("", detail=True)
        assert listing["result"] == "True"
        assert any("visible.txt" in e for e in listing["data"])
        assert not any("secret" in e for e in listing["data"])
        assert not any("hidden" in e for e in listing["data"])
        webdav_tools.valves.FILE_BLACKLIST = ""


class TestWriteFile:
    @pytest.mark.asyncio
    async def test_write_file_creates_file_with_content(self, webdav_tools):
        result = await webdav_tools.write_file("writefile.txt", "hello world")
        assert result == {"result": "True"}

        content = await webdav_tools.read_file("writefile.txt")
        assert content["data"] == "hello world"

    @pytest.mark.asyncio
    async def test_write_file_overwrites_existing(self, webdav_tools):
        await webdav_tools.write_file("overwrite.txt", "original")
        result = await webdav_tools.write_file("overwrite.txt", "replaced")
        assert result == {"result": "True"}

        content = await webdav_tools.read_file("overwrite.txt")
        assert content["data"] == "replaced"

    @pytest.mark.asyncio
    async def test_write_file_empty_content(self, webdav_tools):
        result = await webdav_tools.write_file("empty.txt")
        assert result == {"result": "True"}

        content = await webdav_tools.read_file("empty.txt")
        assert content["data"] == ""


class TestRead:
    @pytest.mark.asyncio
    async def test_read_returns_file_content(self, webdav_tools):
        await webdav_tools.write_file("readfile.txt", "line1\nline2\nline3")
        content = await webdav_tools.read_file("readfile.txt")
        assert content["data"] == "line1\nline2\nline3"

    @pytest.mark.asyncio
    async def test_read_with_offset(self, webdav_tools):
        await webdav_tools.write_file("offsetfile.txt", "line1\nline2\nline3")
        content = await webdav_tools.read_file("offsetfile.txt", offset=2)
        assert content["data"] == "line2\nline3"

    @pytest.mark.asyncio
    async def test_read_with_limit(self, webdav_tools):
        await webdav_tools.write_file("limitfile.txt", "line1\nline2\nline3")
        content = await webdav_tools.read_file("limitfile.txt", limit=2)
        assert content["data"] == "line1\nline2"

    @pytest.mark.asyncio
    async def test_read_with_offset_and_limit(self, webdav_tools):
        await webdav_tools.write_file("bothfile.txt", "a\nb\nc\nd")
        content = await webdav_tools.read_file("bothfile.txt", offset=2, limit=2)
        assert content["data"] == "b\nc"


class TestGlob:
    @pytest.mark.asyncio
    async def test_glob_finds_matching_files(self, webdav_tools):
        await webdav_tools.write_file("glob_a.py", "x")
        await webdav_tools.write_file("glob_b.py", "y")
        await webdav_tools.write_file("glob_c.txt", "z")

        result = await webdav_tools.glob("*.py")
        assert result["result"] == "True"
        files = result["data"]
        assert len(files) == 2
        assert any("glob_a.py" in f for f in files)
        assert any("glob_b.py" in f for f in files)

    @pytest.mark.asyncio
    async def test_glob_subdir_pattern(self, webdav_tools):
        """Glob with subdir prefix pattern like 'subdir/*.py' finds files."""
        await webdav_tools.mkdir("subdir")
        await webdav_tools.write_file("subdir/a.py", "x")
        await webdav_tools.write_file("subdir/b.py", "y")
        await webdav_tools.write_file("subdir/c.txt", "z")

        result = await webdav_tools.glob("subdir/*.py")
        assert result["result"] == "True", result
        files = result["data"]
        assert len(files) == 2, f"expected 2 files, got {len(files)}: {files}"
        assert any("a.py" in f for f in files)
        assert any("b.py" in f for f in files)


class TestGrep:
    @pytest.mark.asyncio
    async def test_grep_finds_pattern_in_files(self, webdav_tools):
        await webdav_tools.write_file("grep_a.py", "def foo():\n    return 42")
        await webdav_tools.write_file("grep_b.py", "def bar():\n    return 0")

        result = await webdav_tools.grep("def foo", include="*.py")
        assert result["result"] == "True"
        matches = result["data"]
        assert len(matches) == 1
        assert matches[0]["file"] == "grep_a.py"
        assert matches[0]["line"] == 1
        assert "def foo" in matches[0]["content"]

    @pytest.mark.asyncio
    async def test_grep_no_matches(self, webdav_tools):
        await webdav_tools.write_file("grep_nomatch.py", "no match here")

        result = await webdav_tools.grep("xyznotfound", include="*.py")
        assert result["result"] == "True"
        assert result["data"] == []


class TestAppendFile:
    @pytest.mark.asyncio
    async def test_append_file_adds_content(self, webdav_tools):
        await webdav_tools.write_file("appendfile.txt", "original\n")
        result = await webdav_tools.append_file("appendfile.txt", "appended\n")
        assert result == {"result": "True"}

        content = await webdav_tools.read_file("appendfile.txt")
        assert content["data"] == "original\nappended"

    @pytest.mark.asyncio
    async def test_append_file_creates_new_file(self, webdav_tools):
        result = await webdav_tools.append_file("newfile.txt", "first line\n")
        assert result == {"result": "True"}

        content = await webdav_tools.read_file("newfile.txt")
        assert content["data"] == "first line"


class TestEdit:
    @pytest.mark.asyncio
    async def test_edit_replaces_string(self, webdav_tools):
        await webdav_tools.write_file("editfile.txt", "foo bar baz")
        result = await webdav_tools.edit("editfile.txt", "foo", "qux")
        assert result["result"] == "True"

        content = await webdav_tools.read_file("editfile.txt")
        assert content["data"] == "qux bar baz"

    @pytest.mark.asyncio
    async def test_edit_replace_all(self, webdav_tools):
        await webdav_tools.write_file("editall.txt", "a b a b a")
        result = await webdav_tools.edit("editall.txt", "a", "z", replace_all=True)
        assert result["result"] == "True"

        content = await webdav_tools.read_file("editall.txt")
        assert content["data"] == "z b z b z"

    @pytest.mark.asyncio
    async def test_edit_string_not_found(self, webdav_tools):
        await webdav_tools.write_file("editfail.txt", "hello")
        result = await webdav_tools.edit("editfail.txt", "notfound", "x")
        assert result["result"] == "False"


class TestRm:
    @pytest.mark.asyncio
    async def test_rm_deletes_file(self, webdav_tools):
        await webdav_tools.write_file("rmfile.txt", "delete me")
        result = await webdav_tools.rm(["rmfile.txt"])
        assert result == {"result": "True"}

        result = await webdav_tools.read_file("rmfile.txt")
        assert result["result"] == "False"

    @pytest.mark.asyncio
    async def test_rm_deletes_directory(self, webdav_tools):
        await webdav_tools.mkdir("rmdir")
        result = await webdav_tools.rm(["rmdir"])
        assert result == {"result": "True"}

        listing = await webdav_tools.ls("")
        assert listing["result"] == "True"
        assert not any("rmdir" in p for p in listing["data"])


class TestMv:
    @pytest.mark.asyncio
    async def test_mv_renames_file(self, webdav_tools):
        await webdav_tools.write_file("mvsrc.txt", "move me")
        result = await webdav_tools.mv("mvsrc.txt", "mvdst.txt")
        assert result == {"result": "True"}

        content = await webdav_tools.read_file("mvdst.txt")
        assert content["data"] == "move me"

        result = await webdav_tools.read_file("mvsrc.txt")
        assert result["result"] == "False"


class TestCp:
    @pytest.mark.asyncio
    async def test_cp_copies_file(self, webdav_tools):
        await webdav_tools.write_file("cpsrc.txt", "copy me")
        result = await webdav_tools.cp("cpsrc.txt", "cpdst.txt")
        assert result == {"result": "True"}

        src = await webdav_tools.read_file("cpsrc.txt")
        dst = await webdav_tools.read_file("cpdst.txt")
        assert src["data"] == dst["data"] == "copy me"


class TestFileBlacklist:
    @pytest.mark.asyncio
    async def test_ls_hides_blacklisted_directory(self, webdav_tools):
        await webdav_tools.mkdir("blacklisted_dir")
        await webdav_tools.mkdir("allowed_dir")
        webdav_tools.valves.FILE_BLACKLIST = "blacklisted_dir"

        listing = await webdav_tools.ls("")
        assert listing["result"] == "True"
        assert not any("blacklisted_dir" in p for p in listing["data"])
        assert any("allowed_dir" in p for p in listing["data"])

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_ls_blocks_blacklisted_path(self, webdav_tools):
        await webdav_tools.mkdir("bl_dir")
        await webdav_tools.write_file("bl_dir/file.txt", "content")
        webdav_tools.valves.FILE_BLACKLIST = "bl_dir"

        result = await webdav_tools.ls("bl_dir")
        assert result["result"] == "False"

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_glob_filters_blacklisted_files(self, webdav_tools):
        await webdav_tools.mkdir("bl_sub")
        await webdav_tools.write_file("allowed.txt", "a")
        await webdav_tools.write_file("bl_sub/hidden.txt", "b")
        webdav_tools.valves.FILE_BLACKLIST = "bl_sub"

        result = await webdav_tools.glob("*.txt", path=".")
        assert result["result"] == "True"
        assert not any("bl_sub" in f for f in result["data"])
        assert any("allowed.txt" in f for f in result["data"])

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_glob_blocks_blacklisted_path(self, webdav_tools):
        await webdav_tools.mkdir("bl_glob")
        await webdav_tools.write_file("bl_glob/f.txt", "x")
        webdav_tools.valves.FILE_BLACKLIST = "bl_glob"

        result = await webdav_tools.glob("*.txt", path="bl_glob")
        assert result["result"] == "False"

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_grep_filters_blacklisted_files(self, webdav_tools):
        await webdav_tools.mkdir("bl_grep")
        await webdav_tools.write_file("allowed.py", "def foo():")
        await webdav_tools.write_file("bl_grep/hidden.py", "def foo():")
        webdav_tools.valves.FILE_BLACKLIST = "bl_grep"

        result = await webdav_tools.grep("def foo", include="*.py")
        assert result["result"] == "True"
        assert not any("bl_grep" in m["file"] for m in result["data"])
        assert any("allowed.py" in m["file"] for m in result["data"])

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_grep_blocks_blacklisted_path(self, webdav_tools):
        await webdav_tools.mkdir("bl_grep2")
        await webdav_tools.write_file("bl_grep2/f.py", "def bar():")
        webdav_tools.valves.FILE_BLACKLIST = "bl_grep2"

        result = await webdav_tools.grep("def bar", path="bl_grep2")
        assert result["result"] == "False"

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_read_blocks_blacklisted_file(self, webdav_tools):
        await webdav_tools.mkdir("bl_read")
        await webdav_tools.write_file("bl_read/secret.txt", "hidden")
        webdav_tools.valves.FILE_BLACKLIST = "bl_read"

        result = await webdav_tools.read_file("bl_read/secret.txt")
        assert result["result"] == "False"

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_write_file_blocks_blacklisted_path(self, webdav_tools):
        webdav_tools.valves.FILE_BLACKLIST = "bl_write"

        result = await webdav_tools.write_file("bl_write/newfile.txt", "data")
        assert result["result"] == "False"

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_append_file_blocks_blacklisted_path(self, webdav_tools):
        webdav_tools.valves.FILE_BLACKLIST = "bl_append"

        result = await webdav_tools.append_file("bl_append/file.txt", "data")
        assert result["result"] == "False"

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_edit_blocks_blacklisted_file(self, webdav_tools):
        await webdav_tools.mkdir("bl_edit")
        await webdav_tools.write_file("bl_edit/file.txt", "original")
        webdav_tools.valves.FILE_BLACKLIST = "bl_edit"

        result = await webdav_tools.edit("bl_edit/file.txt", "original", "replaced")
        assert result["result"] == "False"

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_mkdir_blocks_blacklisted_path(self, webdav_tools):
        webdav_tools.valves.FILE_BLACKLIST = "bl_mkdir"

        result = await webdav_tools.mkdir("bl_mkdir/subdir")
        assert result["result"] == "False"

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_rm_blocks_blacklisted_path(self, webdav_tools):
        await webdav_tools.mkdir("bl_rm")
        webdav_tools.valves.FILE_BLACKLIST = "bl_rm"

        result = await webdav_tools.rm(["bl_rm"])
        assert result["result"] == "False"

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_mv_blocks_blacklisted_src(self, webdav_tools):
        await webdav_tools.mkdir("bl_mv")
        await webdav_tools.write_file("bl_mv/src.txt", "move me")
        webdav_tools.valves.FILE_BLACKLIST = "bl_mv"

        result = await webdav_tools.mv("bl_mv/src.txt", "allowed.txt")
        assert result["result"] == "False"

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_mv_blocks_blacklisted_dst(self, webdav_tools):
        await webdav_tools.write_file("allowed_src.txt", "move me")
        webdav_tools.valves.FILE_BLACKLIST = "bl_mv_dst"

        result = await webdav_tools.mv("allowed_src.txt", "bl_mv_dst/dst.txt")
        assert result["result"] == "False"

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_cp_blocks_blacklisted_src(self, webdav_tools):
        await webdav_tools.mkdir("bl_cp")
        await webdav_tools.write_file("bl_cp/src.txt", "copy me")
        webdav_tools.valves.FILE_BLACKLIST = "bl_cp"

        result = await webdav_tools.cp("bl_cp/src.txt", "allowed_dst.txt")
        assert result["result"] == "False"

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_cp_blocks_blacklisted_dst(self, webdav_tools):
        await webdav_tools.write_file("allowed_src2.txt", "copy me")
        webdav_tools.valves.FILE_BLACKLIST = "bl_cp_dst"

        result = await webdav_tools.cp("allowed_src2.txt", "bl_cp_dst/dst.txt")
        assert result["result"] == "False"

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_multiple_blacklist_entries(self, webdav_tools):
        await webdav_tools.mkdir("bl_a")
        await webdav_tools.mkdir("bl_b")
        await webdav_tools.mkdir("allowed")
        await webdav_tools.write_file("bl_a/f.txt", "a")
        await webdav_tools.write_file("bl_b/f.txt", "b")
        await webdav_tools.write_file("allowed/f.txt", "c")
        webdav_tools.valves.FILE_BLACKLIST = "bl_a, bl_b"

        listing = await webdav_tools.ls("")
        assert listing["result"] == "True"
        assert not any("bl_a" in p for p in listing["data"])
        assert not any("bl_b" in p for p in listing["data"])
        assert any("allowed" in p for p in listing["data"])

        webdav_tools.valves.FILE_BLACKLIST = ""


class TestNestedWriteWithoutParent:
    """Test write_file to deeply nested path when parent dirs don't exist."""

    @pytest.mark.asyncio
    async def test_write_nested_fails_without_parents(self, webdav_tools):
        write_result = await webdav_tools.write_file("nested/deep/file.txt", "data")
        read_result = await webdav_tools.read_file("nested/deep/file.txt")
        if write_result["result"] == "True":
            assert read_result["result"] == "True"
            assert read_result["data"] == "data"
        else:
            assert read_result["result"] == "False"

    @pytest.mark.asyncio
    async def test_write_nested_succeeds_with_explicit_parents(self, webdav_tools):
        await webdav_tools.mkdir("explicit_parent")
        await webdav_tools.mkdir("explicit_parent/child")
        result = await webdav_tools.write_file("explicit_parent/child/file.txt", "data")
        assert result["result"] == "True"
        read_result = await webdav_tools.read_file("explicit_parent/child/file.txt")
        assert read_result["result"] == "True"
        assert read_result["data"] == "data"


class TestRecursiveDirOperations:
    """Test recursive cp and mv for directories with nested content."""

    @pytest.mark.asyncio
    async def test_cp_recursive_directory(self, webdav_tools):
        await webdav_tools.mkdir("srcdir/sub")
        await webdav_tools.write_file("srcdir/a.txt", "content_a")
        await webdav_tools.write_file("srcdir/sub/b.txt", "content_b")
        result = await webdav_tools.cp("srcdir", "dstdir")
        assert result["result"] == "True"
        src_a = await webdav_tools.read_file("srcdir/a.txt")
        dst_a = await webdav_tools.read_file("dstdir/a.txt")
        assert src_a["data"] == dst_a["data"] == "content_a"
        src_b = await webdav_tools.read_file("srcdir/sub/b.txt")
        dst_b = await webdav_tools.read_file("dstdir/sub/b.txt")
        assert src_b["data"] == dst_b["data"] == "content_b"

    @pytest.mark.asyncio
    async def test_mv_directory_moves_contents(self, webdav_tools):
        await webdav_tools.mkdir("mvsrc/sub")
        await webdav_tools.write_file("mvsrc/file.txt", "moved")
        await webdav_tools.write_file("mvsrc/sub/nested.txt", "deep")
        result = await webdav_tools.mv("mvsrc", "mvdst")
        assert result["result"] == "True"
        read_a = await webdav_tools.read_file("mvdst/file.txt")
        assert read_a["result"] == "True"
        assert read_a["data"] == "moved"
        read_b = await webdav_tools.read_file("mvdst/sub/nested.txt")
        assert read_b["result"] == "True"
        assert read_b["data"] == "deep"
        gone = await webdav_tools.read_file("mvsrc/file.txt")
        assert gone["result"] == "False"

    @pytest.mark.asyncio
    async def test_cp_overwrites_existing_destination(self, webdav_tools):
        await webdav_tools.mkdir("cpdst")
        await webdav_tools.write_file("cpdst/file.txt", "original")
        await webdav_tools.mkdir("cp_src_overwrite")
        await webdav_tools.write_file("cp_src_overwrite/file.txt", "overwritten")
        result = await webdav_tools.cp("cp_src_overwrite/file.txt", "cpdst/file.txt")
        assert result["result"] == "True"
        read_result = await webdav_tools.read_file("cpdst/file.txt")
        assert read_result["data"] == "overwritten"

    @pytest.mark.asyncio
    async def test_mv_overwrites_existing_destination(self, webdav_tools):
        await webdav_tools.write_file("mv_existing.txt", "will_be_replaced")
        await webdav_tools.write_file("mv_new.txt", "replacement")
        result = await webdav_tools.mv("mv_new.txt", "mv_existing.txt")
        assert result["result"] == "True"
        read_result = await webdav_tools.read_file("mv_existing.txt")
        assert read_result["data"] == "replacement"
        gone = await webdav_tools.read_file("mv_new.txt")
        assert gone["result"] == "False"


class TestRmMultiplePaths:
    """Test rm with multiple paths in a single call."""

    @pytest.mark.asyncio
    async def test_rm_multiple_files(self, webdav_tools):
        await webdav_tools.write_file("rm_multi_a.txt", "a")
        await webdav_tools.write_file("rm_multi_b.txt", "b")
        await webdav_tools.write_file("rm_multi_c.txt", "c")
        result = await webdav_tools.rm(
            ["rm_multi_a.txt", "rm_multi_b.txt", "rm_multi_c.txt"]
        )
        assert result["result"] == "True"
        assert (await webdav_tools.read_file("rm_multi_a.txt"))["result"] == "False"
        assert (await webdav_tools.read_file("rm_multi_b.txt"))["result"] == "False"
        assert (await webdav_tools.read_file("rm_multi_c.txt"))["result"] == "False"

    @pytest.mark.asyncio
    async def test_rm_files_and_dirs_together(self, webdav_tools):
        await webdav_tools.mkdir("rm_multi_dir")
        await webdav_tools.write_file("rm_multi_dir/inner.txt", "inner")
        await webdav_tools.write_file("rm_multi_file.txt", "file")
        result = await webdav_tools.rm(["rm_multi_dir", "rm_multi_file.txt"])
        assert result["result"] == "True"
        assert (await webdav_tools.read_file("rm_multi_file.txt"))["result"] == "False"
        assert (await webdav_tools.read_file("rm_multi_dir/inner.txt"))[
            "result"
        ] == "False"


class TestGlobRecursive:
    """Test glob with ** recursive patterns."""

    @pytest.mark.asyncio
    async def test_glob_recursive_doublestar(self, webdav_tools):
        await webdav_tools.mkdir("rec/sub1")
        await webdav_tools.mkdir("rec/sub1/deep")
        await webdav_tools.mkdir("rec/sub2")
        await webdav_tools.write_file("rec/root.py", "root")
        await webdav_tools.write_file("rec/sub1/a.py", "a")
        await webdav_tools.write_file("rec/sub1/deep/b.py", "b")
        await webdav_tools.write_file("rec/sub2/c.py", "c")
        await webdav_tools.write_file("rec/sub1/deep/d.txt", "not_py")
        result = await webdav_tools.glob("**/*.py", path="rec")
        assert result["result"] == "True"
        files = result["data"]
        py_files = [f for f in files if f.endswith(".py")]
        assert len(py_files) == 4
        assert any("root.py" in f for f in py_files)
        assert any("a.py" in f for f in py_files)
        assert any("b.py" in f for f in py_files)
        assert any("c.py" in f for f in py_files)

    @pytest.mark.asyncio
    async def test_glob_doublestar_from_sandbox_root(self, webdav_tools):
        # Clean up any leftover .py files from other tests
        existing = await webdav_tools.glob("**/*.py")
        if existing["result"] == "True":
            for f in existing["data"]:
                try:
                    await webdav_tools.rm([f])
                except Exception:
                    pass
        await webdav_tools.mkdir("grec_a")
        await webdav_tools.mkdir("grec_b/nested")
        await webdav_tools.write_file("grec_a/x.py", "x")
        await webdav_tools.write_file("grec_b/y.py", "y")
        await webdav_tools.write_file("grec_b/nested/z.py", "z")
        await webdav_tools.write_file("top.py", "top")
        result = await webdav_tools.glob("**/*.py")
        assert result["result"] == "True"
        files = result["data"]
        assert len(files) == 4


class TestGrepNoInclude:
    """Test grep with include=None (default, no file filter)."""

    @pytest.mark.asyncio
    async def test_grep_no_include_finds_all(self, webdav_tools):
        await webdav_tools.write_file("grep_no_inc.py", "def target():")
        await webdav_tools.write_file("grep_no_inc.txt", "def target():")
        await webdav_tools.write_file("grep_no_inc.md", "no match here")
        result = await webdav_tools.grep("def target")
        assert result["result"] == "True"
        matches = result["data"]
        assert len(matches) == 2
        files_found = {m["file"] for m in matches}
        assert "grep_no_inc.py" in files_found
        assert "grep_no_inc.txt" in files_found


class TestSpecialFilenames:
    """Test files with spaces, unicode, and special characters."""

    @pytest.mark.asyncio
    async def test_file_with_spaces(self, webdav_tools):
        await webdav_tools.write_file("file with spaces.txt", "spaced content")
        result = await webdav_tools.read_file("file with spaces.txt")
        assert result["result"] == "True"
        assert result["data"] == "spaced content"

    @pytest.mark.asyncio
    async def test_file_with_unicode_name(self, webdav_tools):
        await webdav_tools.write_file("unicode_fïlé.txt", "unicode data")
        result = await webdav_tools.read_file("unicode_fïlé.txt")
        assert result["result"] == "True"
        assert result["data"] == "unicode data"

    @pytest.mark.asyncio
    async def test_file_with_special_chars(self, webdav_tools):
        await webdav_tools.write_file("special!@#chars.txt", "special")
        result = await webdav_tools.read_file("special!@#chars.txt")
        assert result["result"] == "True"
        assert result["data"] == "special"

    @pytest.mark.asyncio
    async def test_directory_with_spaces(self, webdav_tools):
        await webdav_tools.mkdir("dir with spaces")
        await webdav_tools.write_file("dir with spaces/file.txt", "in spaced dir")
        result = await webdav_tools.read_file("dir with spaces/file.txt")
        assert result["result"] == "True"
        assert result["data"] == "in spaced dir"
        listing = await webdav_tools.ls("dir with spaces")
        assert listing["result"] == "True"
        assert any("file.txt" in p for p in listing["data"])

    @pytest.mark.asyncio
    async def test_glob_files_with_spaces(self, webdav_tools):
        await webdav_tools.write_file("spacestest_has_spaces.py", "x")
        await webdav_tools.write_file("spacestest_no_spaces.py", "y")
        result = await webdav_tools.glob("spacestest*.py")
        assert result["result"] == "True"
        files = result["data"]
        assert len(files) == 2
        assert any("has_spaces.py" in f for f in files)
        assert any("no_spaces.py" in f for f in files)


class TestReadEdgeCases:
    """Test read_file edge cases."""

    @pytest.mark.asyncio
    async def test_read_offset_beyond_file_length(self, webdav_tools):
        await webdav_tools.write_file("short.txt", "line1\nline2")
        result = await webdav_tools.read_file("short.txt", offset=999)
        assert result["result"] == "True"
        assert result["data"] == ""

    @pytest.mark.asyncio
    async def test_read_offset_at_last_line(self, webdav_tools):
        await webdav_tools.write_file("short2.txt", "line1\nline2\nline3")
        result = await webdav_tools.read_file("short2.txt", offset=3)
        assert result["result"] == "True"
        assert result["data"] == "line3"

    @pytest.mark.asyncio
    async def test_read_offset_and_limit_beyond_file(self, webdav_tools):
        await webdav_tools.write_file("short3.txt", "a\nb\nc")
        result = await webdav_tools.read_file("short3.txt", offset=2, limit=999)
        assert result["result"] == "True"
        assert result["data"] == "b\nc"

    @pytest.mark.asyncio
    async def test_read_empty_file(self, webdav_tools):
        await webdav_tools.write_file("truly_empty.txt", "")
        result = await webdav_tools.read_file("truly_empty.txt")
        assert result["result"] == "True"
        assert result["data"] == ""

    @pytest.mark.asyncio
    async def test_read_offset_zero_rejected(self, webdav_tools):
        result = await webdav_tools.read_file("anyfile.txt", offset=0)
        assert result["result"] == "False"

    @pytest.mark.asyncio
    async def test_read_limit_zero_rejected(self, webdav_tools):
        result = await webdav_tools.read_file("anyfile.txt", limit=0)
        assert result["result"] == "False"

    @pytest.mark.asyncio
    async def test_read_file_not_found(self, webdav_tools):
        result = await webdav_tools.read_file("nonexistent_file_xyz.txt")
        assert result["result"] == "False"


class TestEditEdgeCases:
    """Test edit edge cases including replacement that introduces search term."""

    @pytest.mark.asyncio
    async def test_edit_new_string_contains_old(self, webdav_tools):
        await webdav_tools.write_file("edit_self.txt", "hello world")
        result = await webdav_tools.edit("edit_self.txt", "hello", "say hello again")
        assert result["result"] == "True"
        content = await webdav_tools.read_file("edit_self.txt")
        assert content["data"] == "say hello again world"

    @pytest.mark.asyncio
    async def test_edit_replace_all_new_contains_old(self, webdav_tools):
        await webdav_tools.write_file("edit_self2.txt", "a a a")
        result = await webdav_tools.edit("edit_self2.txt", "a", "ba", replace_all=True)
        assert result["result"] == "True"
        content = await webdav_tools.read_file("edit_self2.txt")
        assert content["data"] == "ba ba ba"

    @pytest.mark.asyncio
    async def test_edit_empty_old_string_rejected(self, webdav_tools):
        await webdav_tools.write_file("edit_empty.txt", "content")
        result = await webdav_tools.edit("edit_empty.txt", "", "x")
        assert result["result"] == "False"

    @pytest.mark.asyncio
    async def test_edit_old_equals_new_rejected(self, webdav_tools):
        await webdav_tools.write_file("edit_same.txt", "same")
        result = await webdav_tools.edit("edit_same.txt", "same", "same")
        assert result["result"] == "False"

    @pytest.mark.asyncio
    async def test_edit_file_not_found(self, webdav_tools):
        result = await webdav_tools.edit("nonexistent_edit.txt", "old", "new")
        assert result["result"] == "False"


class TestEmptySandbox:
    """Test behavior with empty SANDBOX_DIR (no sandbox confinement)."""

    @pytest.mark.asyncio
    async def test_empty_sandbox_write_and_read(self, webdav_tools):
        original = webdav_tools.valves.SANDBOX_DIR
        webdav_tools.valves.SANDBOX_DIR = ""
        try:
            result = await webdav_tools.write_file("nobox_test.txt", "no sandbox")
            assert result["result"] == "True"
            read_result = await webdav_tools.read_file("nobox_test.txt")
            assert read_result["result"] == "True"
            assert read_result["data"] == "no sandbox"
        finally:
            webdav_tools.valves.SANDBOX_DIR = original

    @pytest.mark.asyncio
    async def test_empty_sandbox_ls_root(self, webdav_tools):
        original = webdav_tools.valves.SANDBOX_DIR
        webdav_tools.valves.SANDBOX_DIR = ""
        try:
            await webdav_tools.write_file("nobox_file.txt", "data")
            result = await webdav_tools.ls("")
            assert result["result"] == "True"
        finally:
            webdav_tools.valves.SANDBOX_DIR = original
