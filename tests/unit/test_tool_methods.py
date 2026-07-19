"""Unit tests for Tools internal methods and additional validate_path edge cases."""

import pytest

from owuinc.owuinc import (
    Tools,
    is_blacklisted,
    validate_path,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def valves():
    """Mock valves with a sandbox."""
    from pydantic import BaseModel

    class MockValves(BaseModel):
        SANDBOX_DIR: str = "owuinc"
        FILE_BLACKLIST: str = ""

    return MockValves()


@pytest.fixture
def tool(valves):
    """Minimal Tools instance with mock valves."""
    t = object.__new__(Tools)
    t.valves = valves
    return t


# ---------------------------------------------------------------------------
# validate_path — additional edge cases not covered by test_helpers.py
# ---------------------------------------------------------------------------


class TestValidatePathAdditional:
    """Edge cases for validate_path that agents realistically trigger."""

    def test_null_byte_rejected(self, valves):
        """Null bytes are rejected by validate_path."""
        with pytest.raises(Exception, match="control characters"):
            validate_path("foo\x00bar", valves)

    def test_null_byte_only_rejected(self, valves):
        """Null byte alone is rejected."""
        with pytest.raises(Exception, match="control characters"):
            validate_path("\x00", valves)

    def test_repeated_slashes(self, valves):
        """Many slashes normalize to root prefix (os.path.normpath('') == '.')."""
        result = validate_path("/////", valves)
        assert result in ("owuinc/", "owuinc/.")

    def test_path_with_control_chars_rejected(self, valves):
        """Control characters are rejected by validation."""
        with pytest.raises(Exception, match="control characters"):
            validate_path("foo\x01bar", valves)

    def test_tab_in_path_rejected(self, valves):
        """Embedded tab characters are rejected (leading/trailing stripped
        by .strip(), but embedded are caught as control characters)."""
        with pytest.raises(Exception, match="control characters"):
            validate_path("foo\tbar", valves)

    def test_unicode_path(self, valves):
        """Unicode filenames should pass through."""
        result = validate_path("\u4e16\u754c/file.txt", valves)
        assert "\u4e16\u754c/file.txt" in result

    def test_percent_no_encoding(self, valves):
        """A bare % that isn't valid encoding should survive."""
        result = validate_path("50%discount.txt", valves)
        assert "50%discount.txt" in result

    def test_deeply_nested_path(self, valves):
        """Deep nesting is valid."""
        result = validate_path("a/b/c/d/e/f/g.txt", valves)
        assert result == "owuinc/a/b/c/d/e/f/g.txt"

    def test_path_ends_with_dot(self, valves):
        """Trailing dot is not the same as '.'."""
        result = validate_path("foo.", valves)
        assert result == "owuinc/foo."

    def test_double_dots_in_filename_blocked(self, valves):
        """KNOWN LIMITATION: 'a..b' is blocked because '..' substring
        matches. This is overly aggressive but intentional for security."""
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path("a..b/file.txt", valves)

    def test_three_dots_is_blocked(self, valves):
        """'...' contains '..' so must be blocked."""
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path("...", valves)

    def test_four_dots_blocked(self, valves):
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path("....", valves)

    def test_only_dot_dots_blocked(self, valves):
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path(".../file", valves)

    def test_encoded_dot_dot_blocked(self, valves):
        """URL-encoded '..' should still be blocked."""
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path("%2e%2e/etc/passwd", valves)

    def test_double_encoded_dot_dot_blocked(self, valves):
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path("%252e%252e/etc/passwd", valves)

    def test_triple_encoded_dot_dot_blocked(self, valves):
        """Triple encoding still blocked by iterative decode."""
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path("%25252e%25252e/etc/passwd", valves)


# ---------------------------------------------------------------------------
# _check_blacklisted
# ---------------------------------------------------------------------------


class TestCheckBlacklisted:
    """Direct unit tests for _check_blacklisted (no WebDAV needed)."""

    def test_raises_when_blacklisted(self, tool, valves):
        valves.FILE_BLACKLIST = "secret"
        with pytest.raises(ValueError, match="Access denied"):
            tool._check_blacklisted("secret")

    def test_raises_for_subpath_of_blacklisted(self, tool, valves):
        valves.FILE_BLACKLIST = "secret"
        with pytest.raises(ValueError, match="Access denied"):
            tool._check_blacklisted("secret/nested/file.txt")

    def test_no_raise_when_not_blacklisted(self, tool, valves):
        valves.FILE_BLACKLIST = "secret"
        tool._check_blacklisted("public/file.txt")

    def test_no_raise_on_empty_blacklist(self, tool, valves):
        valves.FILE_BLACKLIST = ""
        tool._check_blacklisted("any/path")

    def test_no_raise_on_empty_path(self, tool, valves):
        valves.FILE_BLACKLIST = "secret"
        tool._check_blacklisted("")

    def test_slash_stripping(self, tool, valves):
        valves.FILE_BLACKLIST = "secret"
        with pytest.raises(ValueError, match="Access denied"):
            tool._check_blacklisted("/secret/")

    def test_error_message_does_not_leak_path(self, tool, valves):
        """Security: error message must NOT contain the path."""
        valves.FILE_BLACKLIST = "secret"
        with pytest.raises(ValueError) as exc_info:
            tool._check_blacklisted("secret/very/private/file.txt")
        err = str(exc_info.value)
        assert "secret" not in err
        assert "private" not in err
        assert "file.txt" not in err

    def test_multiple_blacklist_entries(self, tool, valves):
        valves.FILE_BLACKLIST = "secret,backup,.cache"
        for path in ["secret", "backup/logs", ".cache/tmp"]:
            with pytest.raises(ValueError, match="Access denied"):
                tool._check_blacklisted(path)


# ---------------------------------------------------------------------------
# _is_result_blacklisted
# ---------------------------------------------------------------------------


class TestIsResultBlacklisted:
    """Direct unit tests for _is_result_blacklisted (no WebDAV needed)."""

    def test_returns_true_for_blacklisted(self, tool, valves):
        valves.FILE_BLACKLIST = "secret"
        assert tool._is_result_blacklisted("secret") is True

    def test_returns_true_for_subpath(self, tool, valves):
        valves.FILE_BLACKLIST = "secret"
        assert tool._is_result_blacklisted("secret/file.txt") is True

    def test_returns_false_when_not_blacklisted(self, tool, valves):
        valves.FILE_BLACKLIST = "secret"
        assert tool._is_result_blacklisted("public/file.txt") is False

    def test_returns_false_on_empty_path(self, tool, valves):
        valves.FILE_BLACKLIST = "secret"
        assert tool._is_result_blacklisted("") is False

    def test_slash_stripping(self, tool, valves):
        valves.FILE_BLACKLIST = "secret"
        assert tool._is_result_blacklisted("/secret/") is True


# ---------------------------------------------------------------------------
# _get_rel_path
# ---------------------------------------------------------------------------


class TestGetRelPath:
    """Direct unit tests for _get_rel_path."""

    def test_strips_sandbox_prefix(self, tool, valves):
        result = tool._get_rel_path("owuinc/subdir/file.txt")
        assert result == "subdir/file.txt"

    def test_strips_prefix_with_trailing_content(self, tool, valves):
        result = tool._get_rel_path("owuinc/file.txt")
        assert result == "file.txt"

    def test_empty_rel_path(self, tool, valves):
        result = tool._get_rel_path("owuinc/")
        assert result == ""

    def test_non_matching_prefix_returns_full(self, tool, valves):
        result = tool._get_rel_path("other/file.txt")
        assert result == "other/file.txt"


# ---------------------------------------------------------------------------
# sandbox_prefix property
# ---------------------------------------------------------------------------


class TestSandboxPrefix:
    def test_basic(self, valves):
        t = object.__new__(Tools)
        t.valves = valves
        assert t.sandbox_prefix == "owuinc/"

    def test_trailing_slash_stripped(self):
        from pydantic import BaseModel

        class MockValves(BaseModel):
            SANDBOX_DIR: str = "owuinc/"
            FILE_BLACKLIST: str = ""

        t = object.__new__(Tools)
        t.valves = MockValves()
        assert t.sandbox_prefix == "owuinc/"

    def test_whitespace_stripped(self):
        from pydantic import BaseModel

        class MockValves(BaseModel):
            SANDBOX_DIR: str = "  owuinc  "
            FILE_BLACKLIST: str = ""

        t = object.__new__(Tools)
        t.valves = MockValves()
        assert t.sandbox_prefix == "owuinc/"

    def test_empty_sandbox_prefix(self):
        from pydantic import BaseModel

        class MockValves(BaseModel):
            SANDBOX_DIR: str = ""
            FILE_BLACKLIST: str = ""

        t = object.__new__(Tools)
        t.valves = MockValves()
        assert t.sandbox_prefix == "/"


# ---------------------------------------------------------------------------
# is_blacklisted — additional edge cases
# ---------------------------------------------------------------------------


class TestIsBlacklistedAdditional:
    """Additional is_blacklisted tests for edge cases."""

    def test_partial_match_not_blocked(self):
        """'secret' should NOT block 'secrets'."""
        assert is_blacklisted("secret", "secrets") is False

    def test_partial_match_with_slash_not_blocked(self):
        """'secret' should NOT block 'secrets/file'."""
        assert is_blacklisted("secret", "secrets/file") is False

    def test_substring_no_separator_not_blocked(self):
        """'a/b' should NOT block 'a/bc/file'."""
        assert is_blacklisted("a/b", "a/bc/file.txt") is False

    def test_exact_file_match_blocked(self):
        """Exact match on file name (not just dir)."""
        assert is_blacklisted("file.txt", "file.txt") is True

    def test_multiple_entries_comma(self):
        assert is_blacklisted("a,b,c", "a/file") is True
        assert is_blacklisted("a,b,c", "b/file") is True
        assert is_blacklisted("a,b,c", "c/file") is True
        assert is_blacklisted("a,b,c", "d/file") is False

    def test_spaces_around_entries(self):
        assert is_blacklisted(" secret , backup ", "secret/file") is True

    def test_empty_blacklist_never_matches(self):
        assert is_blacklisted("", "any/path") is False

    def test_none_blacklist(self):
        assert is_blacklisted(None, "any/path") is False


# ---------------------------------------------------------------------------
# _strip_leading_slash
# ---------------------------------------------------------------------------


class TestStripLeadingSlash:
    """Unit tests for _strip_leading_slash helper."""

    def test_strips_single(self):
        from owuinc.owuinc import _strip_leading_slash

        assert _strip_leading_slash("/foo") == "foo"

    def test_strips_multiple(self):
        from owuinc.owuinc import _strip_leading_slash

        assert _strip_leading_slash("///foo") == "foo"

    def test_no_leading_unchanged(self):
        from owuinc.owuinc import _strip_leading_slash

        assert _strip_leading_slash("foo") == "foo"

    def test_empty_string(self):
        from owuinc.owuinc import _strip_leading_slash

        assert _strip_leading_slash("") == ""


# ---------------------------------------------------------------------------
# _webdav_path
# ---------------------------------------------------------------------------


class TestWebdavPath:
    """Unit tests for _webdav_path helper."""

    def test_adds_leading_slash(self):
        from owuinc.owuinc import _webdav_path

        assert _webdav_path("foo") == "/foo"

    def test_preserves_leading_slash(self):
        from owuinc.owuinc import _webdav_path

        assert _webdav_path("/foo") == "/foo"

    def test_empty_string(self):
        from owuinc.owuinc import _webdav_path

        assert _webdav_path("") == "/"
