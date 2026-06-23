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
        assert "2026-" in matching[0] or "2025-" in matching[0]
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

        content = await webdav_tools.read("writefile.txt")
        assert content["data"] == "hello world"

    @pytest.mark.asyncio
    async def test_write_file_overwrites_existing(self, webdav_tools):
        await webdav_tools.write_file("overwrite.txt", "original")
        result = await webdav_tools.write_file("overwrite.txt", "replaced")
        assert result == {"result": "True"}

        content = await webdav_tools.read("overwrite.txt")
        assert content["data"] == "replaced"

    @pytest.mark.asyncio
    async def test_write_file_empty_content(self, webdav_tools):
        result = await webdav_tools.write_file("empty.txt")
        assert result == {"result": "True"}

        content = await webdav_tools.read("empty.txt")
        assert content["data"] == ""


class TestRead:
    @pytest.mark.asyncio
    async def test_read_returns_file_content(self, webdav_tools):
        await webdav_tools.write_file("readfile.txt", "line1\nline2\nline3")
        content = await webdav_tools.read("readfile.txt")
        assert content["data"] == "line1\nline2\nline3"

    @pytest.mark.asyncio
    async def test_read_with_offset(self, webdav_tools):
        await webdav_tools.write_file("offsetfile.txt", "line1\nline2\nline3")
        content = await webdav_tools.read("offsetfile.txt", offset=2)
        assert content["data"] == "line2\nline3"

    @pytest.mark.asyncio
    async def test_read_with_limit(self, webdav_tools):
        await webdav_tools.write_file("limitfile.txt", "line1\nline2\nline3")
        content = await webdav_tools.read("limitfile.txt", limit=2)
        assert content["data"] == "line1\nline2"

    @pytest.mark.asyncio
    async def test_read_with_offset_and_limit(self, webdav_tools):
        await webdav_tools.write_file("bothfile.txt", "a\nb\nc\nd")
        content = await webdav_tools.read("bothfile.txt", offset=2, limit=2)
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

        content = await webdav_tools.read("appendfile.txt")
        assert content["data"] == "original\nappended"

    @pytest.mark.asyncio
    async def test_append_file_creates_new_file(self, webdav_tools):
        result = await webdav_tools.append_file("newfile.txt", "first line\n")
        assert result == {"result": "True"}

        content = await webdav_tools.read("newfile.txt")
        assert content["data"] == "first line"


class TestEdit:
    @pytest.mark.asyncio
    async def test_edit_replaces_string(self, webdav_tools):
        await webdav_tools.write_file("editfile.txt", "foo bar baz")
        result = await webdav_tools.edit("editfile.txt", "foo", "qux")
        assert result["result"] == "True"

        content = await webdav_tools.read("editfile.txt")
        assert content["data"] == "qux bar baz"

    @pytest.mark.asyncio
    async def test_edit_replace_all(self, webdav_tools):
        await webdav_tools.write_file("editall.txt", "a b a b a")
        result = await webdav_tools.edit("editall.txt", "a", "z", replace_all=True)
        assert result["result"] == "True"

        content = await webdav_tools.read("editall.txt")
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

        result = await webdav_tools.read("rmfile.txt")
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

        content = await webdav_tools.read("mvdst.txt")
        assert content["data"] == "move me"

        result = await webdav_tools.read("mvsrc.txt")
        assert result["result"] == "False"


class TestCp:
    @pytest.mark.asyncio
    async def test_cp_copies_file(self, webdav_tools):
        await webdav_tools.write_file("cpsrc.txt", "copy me")
        result = await webdav_tools.cp("cpsrc.txt", "cpdst.txt")
        assert result == {"result": "True"}

        src = await webdav_tools.read("cpsrc.txt")
        dst = await webdav_tools.read("cpdst.txt")
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

        result = await webdav_tools.read("bl_read/secret.txt")
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
