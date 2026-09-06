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
        await webdav_tools.write("ls_testfile.txt", "content")

        listing = await webdav_tools.ls("")
        assert listing["result"] == "True"
        assert any("ls_testdir" in p for p in listing["data"])
        assert any("ls_testfile.txt" in p for p in listing["data"])

    @pytest.mark.asyncio
    async def test_ls_detail_shows_file_info(self, webdav_tools):
        await webdav_tools.write("detail_file.txt", "x" * 100)

        listing = await webdav_tools.ls("", detail=True)
        assert listing["result"] == "True"
        assert any("detail_file.txt" in entry for entry in listing["data"])
        matching = [e for e in listing["data"] if "detail_file.txt" in e]
        assert matching[0].startswith("-rw-r--r--")
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
        assert matching[0].startswith("drwxr-xr-x")
        assert "detail_dir/" in matching[0]

    @pytest.mark.asyncio
    async def test_ls_detail_vs_normal(self, webdav_tools):
        await webdav_tools.write("compare.txt", "data")

        normal = await webdav_tools.ls("")
        detailed = await webdav_tools.ls("", detail=True)

        assert normal["result"] == "True"
        assert detailed["result"] == "True"
        assert any("compare.txt" in p for p in normal["data"])
        assert any("compare.txt" in p for p in detailed["data"])
        assert any("-rw-r--r--" in p for p in detailed["data"])

    @pytest.mark.asyncio
    async def test_ls_detail_hides_blacklisted(self, webdav_tools):
        webdav_tools.valves.FILE_BLACKLIST = "secret"
        await webdav_tools.write("visible.txt", "ok")
        await webdav_tools.mkdir("secret")
        await webdav_tools.write("secret/hidden.txt", "nope")

        listing = await webdav_tools.ls("", detail=True)
        assert listing["result"] == "True"
        assert any("visible.txt" in e for e in listing["data"])
        assert not any("secret" in e for e in listing["data"])
        assert not any("hidden" in e for e in listing["data"])
        webdav_tools.valves.FILE_BLACKLIST = ""


class TestWrite:
    @pytest.mark.asyncio
    async def test_write_creates_file_with_content(self, webdav_tools):
        result = await webdav_tools.write("writefile.txt", "hello world")
        assert result == {"result": "True"}

        content = await webdav_tools.cat("writefile.txt")
        assert content["data"] == "hello world"

    @pytest.mark.asyncio
    async def test_write_overwrites_existing(self, webdav_tools):
        await webdav_tools.write("overwrite.txt", "original")
        result = await webdav_tools.write("overwrite.txt", "replaced")
        assert result == {"result": "True"}

        content = await webdav_tools.cat("overwrite.txt")
        assert content["data"] == "replaced"

    @pytest.mark.asyncio
    async def test_write_empty_content(self, webdav_tools):
        result = await webdav_tools.write("empty.txt")
        assert result == {"result": "True"}

        content = await webdav_tools.cat("empty.txt")
        assert content["data"] == ""


class TestRead:
    @pytest.mark.asyncio
    async def test_read_returns_file_content(self, webdav_tools):
        await webdav_tools.write("readfile.txt", "line1\nline2\nline3")
        content = await webdav_tools.cat("readfile.txt")
        assert content["data"] == "line1\nline2\nline3"

    @pytest.mark.asyncio
    async def test_read_with_offset(self, webdav_tools):
        await webdav_tools.write("offsetfile.txt", "line1\nline2\nline3")
        content = await webdav_tools.cat("offsetfile.txt", offset=2)
        assert content["data"] == "line2\nline3"

    @pytest.mark.asyncio
    async def test_read_with_limit(self, webdav_tools):
        await webdav_tools.write("limitfile.txt", "line1\nline2\nline3")
        content = await webdav_tools.cat("limitfile.txt", limit=2)
        assert content["data"] == "line1\nline2"

    @pytest.mark.asyncio
    async def test_read_with_offset_and_limit(self, webdav_tools):
        await webdav_tools.write("bothfile.txt", "a\nb\nc\nd")
        content = await webdav_tools.cat("bothfile.txt", offset=2, limit=2)
        assert content["data"] == "b\nc"


class TestFind:
    @pytest.mark.asyncio
    async def test_find_finds_matching_files(self, webdav_tools):
        await webdav_tools.write("glob_a.py", "x")
        await webdav_tools.write("glob_b.py", "y")
        await webdav_tools.write("glob_c.txt", "z")

        result = await webdav_tools.find("*.py")
        assert result["result"] == "True"
        files = result["data"]
        assert len(files) == 2
        assert any("glob_a.py" in f for f in files)
        assert any("glob_b.py" in f for f in files)

    @pytest.mark.asyncio
    async def test_find_subdir_pattern(self, webdav_tools):
        """Find with subdir prefix pattern like 'subdir/*.py' finds files."""
        await webdav_tools.mkdir("subdir")
        await webdav_tools.write("subdir/a.py", "x")
        await webdav_tools.write("subdir/b.py", "y")
        await webdav_tools.write("subdir/c.txt", "z")

        result = await webdav_tools.find("subdir/*.py")
        assert result["result"] == "True", result
        files = result["data"]
        assert len(files) == 2, f"expected 2 files, got {len(files)}: {files}"
        assert any("a.py" in f for f in files)
        assert any("b.py" in f for f in files)


class TestGrep:
    @pytest.mark.asyncio
    async def test_grep_finds_pattern_in_files(self, webdav_tools):
        await webdav_tools.write("grep_a.py", "def foo():\n    return 42")
        await webdav_tools.write("grep_b.py", "def bar():\n    return 0")

        result = await webdav_tools.grep("def foo", include="*.py")
        assert result["result"] == "True"
        matches = result["data"]["matches"]
        skipped = result["data"]["skipped"]
        assert len(matches) == 1
        assert matches[0]["file"] == "grep_a.py"
        assert matches[0]["line"] == 1
        assert "def foo" in matches[0]["content"]
        assert skipped == []

    @pytest.mark.asyncio
    async def test_grep_no_matches(self, webdav_tools):
        await webdav_tools.write("grep_nomatch.py", "no match here")

        result = await webdav_tools.grep("xyznotfound", include="*.py")
        assert result["result"] == "True"
        assert result["data"]["matches"] == []
        assert result["data"]["skipped"] == []


class TestAppend:
    @pytest.mark.asyncio
    async def test_append_adds_content(self, webdav_tools):
        await webdav_tools.write("appendfile.txt", "original\n")
        result = await webdav_tools.append("appendfile.txt", "appended\n")
        assert result == {"result": "True"}

        content = await webdav_tools.cat("appendfile.txt")
        assert content["data"] == "original\nappended"

    @pytest.mark.asyncio
    async def test_append_creates_new_file(self, webdav_tools):
        result = await webdav_tools.append("newfile.txt", "first line\n")
        assert result == {"result": "True"}

        content = await webdav_tools.cat("newfile.txt")
        assert content["data"] == "first line"


class TestEdit:
    @pytest.mark.asyncio
    async def test_edit_replaces_string(self, webdav_tools):
        await webdav_tools.write("editfile.txt", "foo bar baz")
        result = await webdav_tools.edit("editfile.txt", "foo", "qux")
        assert result["result"] == "True"

        content = await webdav_tools.cat("editfile.txt")
        assert content["data"] == "qux bar baz"

    @pytest.mark.asyncio
    async def test_edit_replace_all(self, webdav_tools):
        await webdav_tools.write("editall.txt", "a b a b a")
        result = await webdav_tools.edit("editall.txt", "a", "z", replace_all=True)
        assert result["result"] == "True"

        content = await webdav_tools.cat("editall.txt")
        assert content["data"] == "z b z b z"

    @pytest.mark.asyncio
    async def test_edit_string_not_found(self, webdav_tools):
        await webdav_tools.write("editfail.txt", "hello")
        result = await webdav_tools.edit("editfail.txt", "notfound", "x")
        assert result["result"] == "False"


class TestRm:
    @pytest.mark.asyncio
    async def test_rm_deletes_file(self, webdav_tools):
        await webdav_tools.write("rmfile.txt", "delete me")
        result = await webdav_tools.rm(["rmfile.txt"])
        assert result["result"] == "True"
        assert result["data"] == [{"path": "rmfile.txt", "result": "True"}]

        result = await webdav_tools.cat("rmfile.txt")
        assert result["result"] == "False"

    @pytest.mark.asyncio
    async def test_rm_deletes_directory(self, webdav_tools):
        await webdav_tools.mkdir("rmdir")
        result = await webdav_tools.rm(["rmdir"])
        assert result["result"] == "True"
        assert result["data"] == [{"path": "rmdir", "result": "True"}]

        listing = await webdav_tools.ls("")
        assert listing["result"] == "True"
        assert not any("rmdir" in p for p in listing["data"])


class TestMv:
    @pytest.mark.asyncio
    async def test_mv_renames_file(self, webdav_tools):
        await webdav_tools.write("mvsrc.txt", "move me")
        result = await webdav_tools.mv("mvsrc.txt", "mvdst.txt")
        assert result == {"result": "True"}

        content = await webdav_tools.cat("mvdst.txt")
        assert content["data"] == "move me"

        result = await webdav_tools.cat("mvsrc.txt")
        assert result["result"] == "False"


class TestCp:
    @pytest.mark.asyncio
    async def test_cp_copies_file(self, webdav_tools):
        await webdav_tools.write("cpsrc.txt", "copy me")
        result = await webdav_tools.cp("cpsrc.txt", "cpdst.txt")
        assert result == {"result": "True"}

        src = await webdav_tools.cat("cpsrc.txt")
        dst = await webdav_tools.cat("cpdst.txt")
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
        await webdav_tools.write("bl_dir/file.txt", "content")
        webdav_tools.valves.FILE_BLACKLIST = "bl_dir"

        result = await webdav_tools.ls("bl_dir")
        assert result["result"] == "False"

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_glob_filters_blacklisted_files(self, webdav_tools):
        await webdav_tools.mkdir("bl_sub")
        await webdav_tools.write("allowed.txt", "a")
        await webdav_tools.write("bl_sub/hidden.txt", "b")
        webdav_tools.valves.FILE_BLACKLIST = "bl_sub"

        result = await webdav_tools.find("*.txt", path=".")
        assert result["result"] == "True"
        assert not any("bl_sub" in f for f in result["data"])
        assert any("allowed.txt" in f for f in result["data"])

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_glob_blocks_blacklisted_path(self, webdav_tools):
        await webdav_tools.mkdir("bl_glob")
        await webdav_tools.write("bl_glob/f.txt", "x")
        webdav_tools.valves.FILE_BLACKLIST = "bl_glob"

        result = await webdav_tools.find("*.txt", path="bl_glob")
        assert result["result"] == "False"

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_grep_filters_blacklisted_files(self, webdav_tools):
        await webdav_tools.mkdir("bl_grep")
        await webdav_tools.write("allowed.py", "def foo():")
        await webdav_tools.write("bl_grep/hidden.py", "def foo():")
        webdav_tools.valves.FILE_BLACKLIST = "bl_grep"

        result = await webdav_tools.grep("def foo", include="*.py")
        assert result["result"] == "True"
        assert not any("bl_grep" in m["file"] for m in result["data"]["matches"])
        assert any("allowed.py" in m["file"] for m in result["data"]["matches"])

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_grep_blocks_blacklisted_path(self, webdav_tools):
        await webdav_tools.mkdir("bl_grep2")
        await webdav_tools.write("bl_grep2/f.py", "def bar():")
        webdav_tools.valves.FILE_BLACKLIST = "bl_grep2"

        result = await webdav_tools.grep("def bar", path="bl_grep2")
        assert result["result"] == "False"

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_read_blocks_blacklisted_file(self, webdav_tools):
        await webdav_tools.mkdir("bl_read")
        await webdav_tools.write("bl_read/secret.txt", "hidden")
        webdav_tools.valves.FILE_BLACKLIST = "bl_read"

        result = await webdav_tools.cat("bl_read/secret.txt")
        assert result["result"] == "False"

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_write_blocks_blacklisted_path(self, webdav_tools):
        webdav_tools.valves.FILE_BLACKLIST = "bl_write"

        result = await webdav_tools.write("bl_write/newfile.txt", "data")
        assert result["result"] == "False"

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_append_blocks_blacklisted_path(self, webdav_tools):
        webdav_tools.valves.FILE_BLACKLIST = "bl_append"

        result = await webdav_tools.append("bl_append/file.txt", "data")
        assert result["result"] == "False"

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_edit_blocks_blacklisted_file(self, webdav_tools):
        await webdav_tools.mkdir("bl_edit")
        await webdav_tools.write("bl_edit/file.txt", "original")
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
        assert result["result"] == "True"
        entry = result["data"][0]
        assert entry["path"] == "bl_rm"
        assert entry["result"] == "False"
        assert entry["details"] == "Access denied"

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_mv_blocks_blacklisted_src(self, webdav_tools):
        await webdav_tools.mkdir("bl_mv")
        await webdav_tools.write("bl_mv/src.txt", "move me")
        webdav_tools.valves.FILE_BLACKLIST = "bl_mv"

        result = await webdav_tools.mv("bl_mv/src.txt", "allowed.txt")
        assert result["result"] == "False"

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_mv_blocks_blacklisted_dst(self, webdav_tools):
        await webdav_tools.write("allowed_src.txt", "move me")
        webdav_tools.valves.FILE_BLACKLIST = "bl_mv_dst"

        result = await webdav_tools.mv("allowed_src.txt", "bl_mv_dst/dst.txt")
        assert result["result"] == "False"

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_cp_blocks_blacklisted_src(self, webdav_tools):
        await webdav_tools.mkdir("bl_cp")
        await webdav_tools.write("bl_cp/src.txt", "copy me")
        webdav_tools.valves.FILE_BLACKLIST = "bl_cp"

        result = await webdav_tools.cp("bl_cp/src.txt", "allowed_dst.txt")
        assert result["result"] == "False"

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_cp_blocks_blacklisted_dst(self, webdav_tools):
        await webdav_tools.write("allowed_src2.txt", "copy me")
        webdav_tools.valves.FILE_BLACKLIST = "bl_cp_dst"

        result = await webdav_tools.cp("allowed_src2.txt", "bl_cp_dst/dst.txt")
        assert result["result"] == "False"

        webdav_tools.valves.FILE_BLACKLIST = ""

    @pytest.mark.asyncio
    async def test_multiple_blacklist_entries(self, webdav_tools):
        await webdav_tools.mkdir("bl_a")
        await webdav_tools.mkdir("bl_b")
        await webdav_tools.mkdir("allowed")
        await webdav_tools.write("bl_a/f.txt", "a")
        await webdav_tools.write("bl_b/f.txt", "b")
        await webdav_tools.write("allowed/f.txt", "c")
        webdav_tools.valves.FILE_BLACKLIST = "bl_a, bl_b"

        listing = await webdav_tools.ls("")
        assert listing["result"] == "True"
        assert not any("bl_a" in p for p in listing["data"])
        assert not any("bl_b" in p for p in listing["data"])
        assert any("allowed" in p for p in listing["data"])

        webdav_tools.valves.FILE_BLACKLIST = ""


class TestNestedWriteWithoutParent:
    """Test write to deeply nested path when parent dirs don't exist."""

    @pytest.mark.asyncio
    async def test_write_nested_fails_without_parents(self, webdav_tools):
        write_result = await webdav_tools.write("nested/deep/file.txt", "data")
        read_result = await webdav_tools.cat("nested/deep/file.txt")
        if write_result["result"] == "True":
            assert read_result["result"] == "True"
            assert read_result["data"] == "data"
        else:
            assert read_result["result"] == "False"

    @pytest.mark.asyncio
    async def test_write_nested_succeeds_with_explicit_parents(self, webdav_tools):
        await webdav_tools.mkdir("explicit_parent")
        await webdav_tools.mkdir("explicit_parent/child")
        result = await webdav_tools.write("explicit_parent/child/file.txt", "data")
        assert result["result"] == "True"
        read_result = await webdav_tools.cat("explicit_parent/child/file.txt")
        assert read_result["result"] == "True"
        assert read_result["data"] == "data"


class TestRecursiveDirOperations:
    """Test recursive cp and mv for directories with nested content."""

    @pytest.mark.asyncio
    async def test_cp_recursive_directory(self, webdav_tools):
        await webdav_tools.mkdir("srcdir/sub")
        await webdav_tools.write("srcdir/a.txt", "content_a")
        await webdav_tools.write("srcdir/sub/b.txt", "content_b")
        result = await webdav_tools.cp("srcdir", "dstdir")
        assert result["result"] == "True"
        src_a = await webdav_tools.cat("srcdir/a.txt")
        dst_a = await webdav_tools.cat("dstdir/a.txt")
        assert src_a["data"] == dst_a["data"] == "content_a"
        src_b = await webdav_tools.cat("srcdir/sub/b.txt")
        dst_b = await webdav_tools.cat("dstdir/sub/b.txt")
        assert src_b["data"] == dst_b["data"] == "content_b"

    @pytest.mark.asyncio
    async def test_mv_directory_moves_contents(self, webdav_tools):
        await webdav_tools.mkdir("mvsrc/sub")
        await webdav_tools.write("mvsrc/file.txt", "moved")
        await webdav_tools.write("mvsrc/sub/nested.txt", "deep")
        result = await webdav_tools.mv("mvsrc", "mvdst")
        assert result["result"] == "True"
        read_a = await webdav_tools.cat("mvdst/file.txt")
        assert read_a["result"] == "True"
        assert read_a["data"] == "moved"
        read_b = await webdav_tools.cat("mvdst/sub/nested.txt")
        assert read_b["result"] == "True"
        assert read_b["data"] == "deep"
        gone = await webdav_tools.cat("mvsrc/file.txt")
        assert gone["result"] == "False"

    @pytest.mark.asyncio
    async def test_cp_overwrites_existing_destination(self, webdav_tools):
        await webdav_tools.mkdir("cpdst")
        await webdav_tools.write("cpdst/file.txt", "original")
        await webdav_tools.mkdir("cp_src_overwrite")
        await webdav_tools.write("cp_src_overwrite/file.txt", "overwritten")
        result = await webdav_tools.cp("cp_src_overwrite/file.txt", "cpdst/file.txt")
        assert result["result"] == "True"
        read_result = await webdav_tools.cat("cpdst/file.txt")
        assert read_result["data"] == "overwritten"

    @pytest.mark.asyncio
    async def test_mv_overwrites_existing_destination(self, webdav_tools):
        await webdav_tools.write("mv_existing.txt", "will_be_replaced")
        await webdav_tools.write("mv_new.txt", "replacement")
        result = await webdav_tools.mv("mv_new.txt", "mv_existing.txt")
        assert result["result"] == "True"
        read_result = await webdav_tools.cat("mv_existing.txt")
        assert read_result["data"] == "replacement"
        gone = await webdav_tools.cat("mv_new.txt")
        assert gone["result"] == "False"


class TestRmMultiplePaths:
    """Test rm with multiple paths in a single call."""

    @pytest.mark.asyncio
    async def test_rm_multiple_files(self, webdav_tools):
        await webdav_tools.write("rm_multi_a.txt", "a")
        await webdav_tools.write("rm_multi_b.txt", "b")
        await webdav_tools.write("rm_multi_c.txt", "c")
        result = await webdav_tools.rm(
            ["rm_multi_a.txt", "rm_multi_b.txt", "rm_multi_c.txt"]
        )
        assert result["result"] == "True"
        assert (await webdav_tools.cat("rm_multi_a.txt"))["result"] == "False"
        assert (await webdav_tools.cat("rm_multi_b.txt"))["result"] == "False"
        assert (await webdav_tools.cat("rm_multi_c.txt"))["result"] == "False"

    @pytest.mark.asyncio
    async def test_rm_files_and_dirs_together(self, webdav_tools):
        await webdav_tools.mkdir("rm_multi_dir")
        await webdav_tools.write("rm_multi_dir/inner.txt", "inner")
        await webdav_tools.write("rm_multi_file.txt", "file")
        result = await webdav_tools.rm(["rm_multi_dir", "rm_multi_file.txt"])
        assert result["result"] == "True"
        assert (await webdav_tools.cat("rm_multi_file.txt"))["result"] == "False"
        assert (await webdav_tools.cat("rm_multi_dir/inner.txt"))["result"] == "False"


class TestFindRecursive:
    """Test find with ** recursive patterns."""

    @pytest.mark.asyncio
    async def test_find_recursive_doublestar(self, webdav_tools):
        await webdav_tools.mkdir("rec/sub1")
        await webdav_tools.mkdir("rec/sub1/deep")
        await webdav_tools.mkdir("rec/sub2")
        await webdav_tools.write("rec/root.py", "root")
        await webdav_tools.write("rec/sub1/a.py", "a")
        await webdav_tools.write("rec/sub1/deep/b.py", "b")
        await webdav_tools.write("rec/sub2/c.py", "c")
        await webdav_tools.write("rec/sub1/deep/d.txt", "not_py")
        result = await webdav_tools.find("**/*.py", path="rec")
        assert result["result"] == "True"
        files = result["data"]
        py_files = [f for f in files if f.endswith(".py")]
        assert len(py_files) == 4
        assert any("root.py" in f for f in py_files)
        assert any("a.py" in f for f in py_files)
        assert any("b.py" in f for f in py_files)
        assert any("c.py" in f for f in py_files)

    @pytest.mark.asyncio
    async def test_find_doublestar_from_sandbox_root(self, webdav_tools):
        # Clean up any leftover .py files from other tests
        existing = await webdav_tools.find("**/*.py")
        if existing["result"] == "True":
            for f in existing["data"]:
                try:
                    await webdav_tools.rm([f])
                except Exception:
                    pass
        await webdav_tools.mkdir("grec_a")
        await webdav_tools.mkdir("grec_b/nested")
        await webdav_tools.write("grec_a/x.py", "x")
        await webdav_tools.write("grec_b/y.py", "y")
        await webdav_tools.write("grec_b/nested/z.py", "z")
        await webdav_tools.write("top.py", "top")
        result = await webdav_tools.find("**/*.py")
        assert result["result"] == "True"
        files = result["data"]
        assert len(files) == 4


class TestGrepNoInclude:
    """Test grep with include=None (default, no file filter)."""

    @pytest.mark.asyncio
    async def test_grep_no_include_finds_all(self, webdav_tools):
        await webdav_tools.write("grep_no_inc.py", "def target():")
        await webdav_tools.write("grep_no_inc.txt", "def target():")
        await webdav_tools.write("grep_no_inc.md", "no match here")
        result = await webdav_tools.grep("def target")
        assert result["result"] == "True"
        matches = result["data"]["matches"]
        assert len(matches) == 2
        files_found = {m["file"] for m in matches}
        assert "grep_no_inc.py" in files_found
        assert "grep_no_inc.txt" in files_found


class TestSpecialFilenames:
    """Test files with spaces, unicode, and special characters."""

    @pytest.mark.asyncio
    async def test_file_with_spaces(self, webdav_tools):
        await webdav_tools.write("file with spaces.txt", "spaced content")
        result = await webdav_tools.cat("file with spaces.txt")
        assert result["result"] == "True"
        assert result["data"] == "spaced content"

    @pytest.mark.asyncio
    async def test_file_with_unicode_name(self, webdav_tools):
        await webdav_tools.write("unicode_fïlé.txt", "unicode data")
        result = await webdav_tools.cat("unicode_fïlé.txt")
        assert result["result"] == "True"
        assert result["data"] == "unicode data"

    @pytest.mark.asyncio
    async def test_file_with_special_chars(self, webdav_tools):
        await webdav_tools.write("special!@#chars.txt", "special")
        result = await webdav_tools.cat("special!@#chars.txt")
        assert result["result"] == "True"
        assert result["data"] == "special"

    @pytest.mark.asyncio
    async def test_directory_with_spaces(self, webdav_tools):
        await webdav_tools.mkdir("dir with spaces")
        await webdav_tools.write("dir with spaces/file.txt", "in spaced dir")
        result = await webdav_tools.cat("dir with spaces/file.txt")
        assert result["result"] == "True"
        assert result["data"] == "in spaced dir"
        listing = await webdav_tools.ls("dir with spaces")
        assert listing["result"] == "True"
        assert any("file.txt" in p for p in listing["data"])

    @pytest.mark.asyncio
    async def test_find_files_with_spaces(self, webdav_tools):
        await webdav_tools.write("spacestest_has_spaces.py", "x")
        await webdav_tools.write("spacestest_no_spaces.py", "y")
        result = await webdav_tools.find("spacestest*.py")
        assert result["result"] == "True"
        files = result["data"]
        assert len(files) == 2
        assert any("has_spaces.py" in f for f in files)
        assert any("no_spaces.py" in f for f in files)


class TestReadEdgeCases:
    """Test cat edge cases."""

    @pytest.mark.asyncio
    async def test_cat_offset_beyond_file_length(self, webdav_tools):
        await webdav_tools.write("short.txt", "line1\nline2")
        result = await webdav_tools.cat("short.txt", offset=999)
        assert result["result"] == "True"
        assert result["data"] == ""

    @pytest.mark.asyncio
    async def test_cat_offset_at_last_line(self, webdav_tools):
        await webdav_tools.write("short2.txt", "line1\nline2\nline3")
        result = await webdav_tools.cat("short2.txt", offset=3)
        assert result["result"] == "True"
        assert result["data"] == "line3"

    @pytest.mark.asyncio
    async def test_cat_offset_and_limit_beyond_file(self, webdav_tools):
        await webdav_tools.write("short3.txt", "a\nb\nc")
        result = await webdav_tools.cat("short3.txt", offset=2, limit=999)
        assert result["result"] == "True"
        assert result["data"] == "b\nc"

    @pytest.mark.asyncio
    async def test_cat_empty_file(self, webdav_tools):
        await webdav_tools.write("truly_empty.txt", "")
        result = await webdav_tools.cat("truly_empty.txt")
        assert result["result"] == "True"
        assert result["data"] == ""

    @pytest.mark.asyncio
    async def test_cat_offset_zero_rejected(self, webdav_tools):
        result = await webdav_tools.cat("anyfile.txt", offset=0)
        assert result["result"] == "False"

    @pytest.mark.asyncio
    async def test_cat_limit_zero_rejected(self, webdav_tools):
        result = await webdav_tools.cat("anyfile.txt", limit=0)
        assert result["result"] == "False"

    @pytest.mark.asyncio
    async def test_cat_file_not_found(self, webdav_tools):
        result = await webdav_tools.cat("nonexistent_file_xyz.txt")
        assert result["result"] == "False"


class TestEditEdgeCases:
    """Test edit edge cases including replacement that introduces search term."""

    @pytest.mark.asyncio
    async def test_edit_new_string_contains_old(self, webdav_tools):
        await webdav_tools.write("edit_self.txt", "hello world")
        result = await webdav_tools.edit("edit_self.txt", "hello", "say hello again")
        assert result["result"] == "True"
        content = await webdav_tools.cat("edit_self.txt")
        assert content["data"] == "say hello again world"

    @pytest.mark.asyncio
    async def test_edit_replace_all_new_contains_old(self, webdav_tools):
        await webdav_tools.write("edit_self2.txt", "a a a")
        result = await webdav_tools.edit("edit_self2.txt", "a", "ba", replace_all=True)
        assert result["result"] == "True"
        content = await webdav_tools.cat("edit_self2.txt")
        assert content["data"] == "ba ba ba"

    @pytest.mark.asyncio
    async def test_edit_empty_old_string_rejected(self, webdav_tools):
        await webdav_tools.write("edit_empty.txt", "content")
        result = await webdav_tools.edit("edit_empty.txt", "", "x")
        assert result["result"] == "False"

    @pytest.mark.asyncio
    async def test_edit_old_equals_new_rejected(self, webdav_tools):
        await webdav_tools.write("edit_same.txt", "same")
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
            result = await webdav_tools.write("nobox_test.txt", "no sandbox")
            assert result["result"] == "True"
            read_result = await webdav_tools.cat("nobox_test.txt")
            assert read_result["result"] == "True"
            assert read_result["data"] == "no sandbox"
        finally:
            webdav_tools.valves.SANDBOX_DIR = original

    @pytest.mark.asyncio
    async def test_empty_sandbox_ls_root(self, webdav_tools):
        original = webdav_tools.valves.SANDBOX_DIR
        webdav_tools.valves.SANDBOX_DIR = ""
        try:
            await webdav_tools.write("nobox_file.txt", "data")
            result = await webdav_tools.ls("")
            assert result["result"] == "True"
        finally:
            webdav_tools.valves.SANDBOX_DIR = original


class TestBinaryFileHandling:
    """Binary files are explicitly rejected with a clear error."""

    @staticmethod
    async def _create_binary_file(webdav_tools, filename, raw_bytes):
        from io import BytesIO

        from owuinc.owuinc import _webdav_path, validate_path

        raw_client = webdav_tools._webdav_client()
        try:
            await webdav_tools._ensure_sandbox(raw_client)
            full = validate_path(filename, webdav_tools.valves)
            await raw_client.resource(_webdav_path(full)).write_to(BytesIO(raw_bytes))
        finally:
            await raw_client.close()

    @pytest.mark.asyncio
    async def test_cat_binary_rejected(self, webdav_tools):
        await self._create_binary_file(
            webdav_tools, "binary.dat", b"\x00\x01\x02\xff\xfe"
        )
        result = await webdav_tools.cat("binary.dat")
        assert result["result"] == "False"
        assert "text" in result["details"].lower()

    @pytest.mark.asyncio
    async def test_append_binary_rejected(self, webdav_tools):
        await self._create_binary_file(
            webdav_tools, "binary_append.dat", b"\x00\x01\x02\xff\xfe"
        )
        result = await webdav_tools.append("binary_append.dat", "hello")
        assert result["result"] == "False"
        assert "text" in result["details"].lower()

    @pytest.mark.asyncio
    async def test_edit_binary_rejected(self, webdav_tools):
        await self._create_binary_file(
            webdav_tools, "binary_edit.dat", b"\x00\x01\x02\xff\xfe"
        )
        result = await webdav_tools.edit("binary_edit.dat", "\x00", "x")
        assert result["result"] == "False"
        assert "text" in result["details"].lower()

    @pytest.mark.asyncio
    async def test_grep_reports_skipped_binary(self, webdav_tools):
        await webdav_tools.mkdir("grep_bin_test")
        await self._create_binary_file(
            webdav_tools, "grep_bin_test/binary_skip.dat", b"\xff\xfe\x80\x81"
        )
        await webdav_tools.write("grep_bin_test/skip_text.txt", "skip target here")
        result = await webdav_tools.grep("skip target", path="grep_bin_test")
        assert result["result"] == "True"
        data = result["data"]
        assert "matches" in data
        assert "skipped" in data
        assert len(data["matches"]) == 1
        assert "skip_text.txt" in data["matches"][0]["file"]
        assert any("binary_skip.dat" in f for f in data["skipped"])

    @pytest.mark.asyncio
    async def test_grep_skipped_empty_when_no_binary(self, webdav_tools):
        await webdav_tools.mkdir("grep_clean_test")
        await webdav_tools.write("grep_clean_test/only_text.txt", "solo target line")
        result = await webdav_tools.grep("solo target", path="grep_clean_test")
        assert result["result"] == "True"
        data = result["data"]
        assert data["skipped"] == []
        assert len(data["matches"]) >= 1


class TestWebDavLocking:
    """Test that edit() and append() use WebDAV locking."""

    @pytest.mark.asyncio
    async def test_edit_holds_lock_during_operation(self, webdav_tools):
        await webdav_tools.write("lockedit.txt", "original content here")
        result = await webdav_tools.edit("lockedit.txt", "original", "modified")
        assert result["result"] == "True"
        content = await webdav_tools.cat("lockedit.txt")
        assert content["data"] == "modified content here"

    @pytest.mark.asyncio
    async def test_append_holds_lock(self, webdav_tools):
        await webdav_tools.write("lockappend.txt", "line1\n")
        result = await webdav_tools.append("lockappend.txt", "line2\n")
        assert result["result"] == "True"
        content = await webdav_tools.cat("lockappend.txt")
        assert content["data"] == "line1\nline2"

    @pytest.mark.asyncio
    async def test_append_creates_new_file(self, webdav_tools):
        result = await webdav_tools.append("locknewfile.txt", "first line\n")
        assert result["result"] == "True"
        content = await webdav_tools.cat("locknewfile.txt")
        assert content["data"] == "first line"

    @pytest.mark.asyncio
    async def test_edit_conflict_when_locked(self, webdav_tools):
        await webdav_tools.write("lockconflict.txt", "a b c")
        from owuinc.owuinc import _webdav_path, validate_path

        client = webdav_tools._webdav_client()
        try:
            res_path = _webdav_path(
                validate_path("lockconflict.txt", webdav_tools.valves)
            )
            lock = await client.lock(res_path, timeout=10)
            result = await webdav_tools.edit("lockconflict.txt", "a", "x")
            assert result["result"] == "False"
            assert "locked" in result["details"].lower()
            # Release the lock
            await lock.close()
        finally:
            await client.close()
