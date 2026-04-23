"""Integration tests for WebDAV file operations using WsgiDAV."""


class TestMkdir:
    def test_mkdir_creates_directory(self, webdav_tools):
        result = webdav_tools.mkdir("testdir")
        assert result == {"result": "True"}

        listing = webdav_tools.ls("")
        assert listing["result"] == "True"
        assert any("testdir" in p for p in listing["data"])


class TestLs:
    def test_ls_lists_files_and_directories(self, webdav_tools):
        webdav_tools.mkdir("ls_testdir")
        webdav_tools.write_file("ls_testfile.txt", "content")

        listing = webdav_tools.ls("")
        assert listing["result"] == "True"
        assert any("ls_testdir" in p for p in listing["data"])
        assert any("ls_testfile.txt" in p for p in listing["data"])


class TestWriteFile:
    def test_write_file_creates_file_with_content(self, webdav_tools):
        result = webdav_tools.write_file("writefile.txt", "hello world")
        assert result == {"result": "True"}

        content = webdav_tools.read("writefile.txt")
        assert content["data"] == "hello world"

    def test_write_file_overwrites_existing(self, webdav_tools):
        webdav_tools.write_file("overwrite.txt", "original")
        result = webdav_tools.write_file("overwrite.txt", "replaced")
        assert result == {"result": "True"}

        content = webdav_tools.read("overwrite.txt")
        assert content["data"] == "replaced"

    def test_write_file_empty_content(self, webdav_tools):
        result = webdav_tools.write_file("empty.txt")
        assert result == {"result": "True"}

        content = webdav_tools.read("empty.txt")
        assert content["data"] == ""


class TestRead:
    def test_read_returns_file_content(self, webdav_tools):
        webdav_tools.write_file("readfile.txt", "line1\nline2\nline3")
        content = webdav_tools.read("readfile.txt")
        assert content["data"] == "line1\nline2\nline3"

    def test_read_with_offset(self, webdav_tools):
        webdav_tools.write_file("offsetfile.txt", "line1\nline2\nline3")
        content = webdav_tools.read("offsetfile.txt", offset=2)
        assert content["data"] == "line2\nline3"

    def test_read_with_limit(self, webdav_tools):
        webdav_tools.write_file("limitfile.txt", "line1\nline2\nline3")
        content = webdav_tools.read("limitfile.txt", limit=2)
        assert content["data"] == "line1\nline2"

    def test_read_with_offset_and_limit(self, webdav_tools):
        webdav_tools.write_file("bothfile.txt", "a\nb\nc\nd")
        content = webdav_tools.read("bothfile.txt", offset=2, limit=2)
        assert content["data"] == "b\nc"


class TestGlob:
    def test_glob_finds_matching_files(self, webdav_tools):
        webdav_tools.write_file("glob_a.py", "x")
        webdav_tools.write_file("glob_b.py", "y")
        webdav_tools.write_file("glob_c.txt", "z")

        result = webdav_tools.glob("*.py")
        assert result["result"] == "True"
        files = result["data"]
        assert len(files) == 2
        assert any("glob_a.py" in f for f in files)
        assert any("glob_b.py" in f for f in files)


class TestGrep:
    def test_grep_finds_pattern_in_files(self, webdav_tools):
        webdav_tools.write_file("grep_a.py", "def foo():\n    return 42")
        webdav_tools.write_file("grep_b.py", "def bar():\n    return 0")

        result = webdav_tools.grep("def foo", include="*.py")
        assert result["result"] == "True"
        matches = result["data"]
        assert len(matches) == 1
        assert matches[0]["file"] == "grep_a.py"
        assert matches[0]["line"] == 1
        assert "def foo" in matches[0]["content"]

    def test_grep_no_matches(self, webdav_tools):
        webdav_tools.write_file("grep_nomatch.py", "no match here")

        result = webdav_tools.grep("xyznotfound", include="*.py")
        assert result["result"] == "True"
        assert result["data"] == []


class TestAppendFile:
    def test_append_file_adds_content(self, webdav_tools):
        webdav_tools.write_file("appendfile.txt", "original\n")
        result = webdav_tools.append_file("appendfile.txt", "appended\n")
        assert result == {"result": "True"}

        content = webdav_tools.read("appendfile.txt")
        assert content["data"] == "original\nappended"

    def test_append_file_creates_new_file(self, webdav_tools):
        result = webdav_tools.append_file("newfile.txt", "first line\n")
        assert result == {"result": "True"}

        content = webdav_tools.read("newfile.txt")
        assert content["data"] == "first line"


class TestEdit:
    def test_edit_replaces_string(self, webdav_tools):
        webdav_tools.write_file("editfile.txt", "foo bar baz")
        result = webdav_tools.edit("editfile.txt", "foo", "qux")
        assert result["result"] == "True"

        content = webdav_tools.read("editfile.txt")
        assert content["data"] == "qux bar baz"

    def test_edit_replace_all(self, webdav_tools):
        webdav_tools.write_file("editall.txt", "a b a b a")
        result = webdav_tools.edit("editall.txt", "a", "z", replace_all=True)
        assert result["result"] == "True"

        content = webdav_tools.read("editall.txt")
        assert content["data"] == "z b z b z"

    def test_edit_string_not_found(self, webdav_tools):
        webdav_tools.write_file("editfail.txt", "hello")
        result = webdav_tools.edit("editfail.txt", "notfound", "x")
        assert result["result"] == "False"


class TestRm:
    def test_rm_deletes_file(self, webdav_tools):
        webdav_tools.write_file("rmfile.txt", "delete me")
        result = webdav_tools.rm(["rmfile.txt"])
        assert result == {"result": "True"}

        result = webdav_tools.read("rmfile.txt")
        assert result["result"] == "False"

    def test_rm_deletes_directory(self, webdav_tools):
        webdav_tools.mkdir("rmdir")
        result = webdav_tools.rm(["rmdir"])
        assert result == {"result": "True"}

        listing = webdav_tools.ls("")
        assert listing["result"] == "True"
        assert not any("rmdir" in p for p in listing["data"])


class TestMv:
    def test_mv_renames_file(self, webdav_tools):
        webdav_tools.write_file("mvsrc.txt", "move me")
        result = webdav_tools.mv("mvsrc.txt", "mvdst.txt")
        assert result == {"result": "True"}

        content = webdav_tools.read("mvdst.txt")
        assert content["data"] == "move me"

        result = webdav_tools.read("mvsrc.txt")
        assert result["result"] == "False"


class TestCp:
    def test_cp_copies_file(self, webdav_tools):
        webdav_tools.write_file("cpsrc.txt", "copy me")
        result = webdav_tools.cp("cpsrc.txt", "cpdst.txt")
        assert result == {"result": "True"}

        src = webdav_tools.read("cpsrc.txt")
        dst = webdav_tools.read("cpdst.txt")
        assert src["data"] == dst["data"] == "copy me"
