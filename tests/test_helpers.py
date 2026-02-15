"""
Helper function tests for validate_path
Tests sandbox security, path traversal prevention, and normalization
"""

import pytest

from owuinc.owuinc import Tools


@pytest.fixture
def tools():
    """Create Tools instance with test sandbox"""
    T = Tools()
    T.valves.SANDBOX_DIR = "/test/sandbox"
    return T


class TestValidatePathSanitization:
    """Test path normalization and whitespace handling"""

    def test_empty_path_returns_prefix(self, tools):
        assert tools.H.validate_path("", "/test/sandbox") == "/test/sandbox/"

    def test_whitespace_only_path_strips_to_empty(self, tools):
        assert tools.H.validate_path("   ", "/test/sandbox") == "/test/sandbox/"

    def test_path_with_trailing_slash(self, tools):
        assert tools.H.validate_path("foo/", "/test/sandbox") == "/test/sandbox/foo"

    def test_path_with_leading_slash(self, tools):
        # Leading slash creates double slash after prefix
        assert tools.H.validate_path("/foo", "/test/sandbox") == "/test/sandbox//foo"

    def test_dot_returns_prefix(self, tools):
        assert tools.H.validate_path(".", "/test/sandbox") == "/test/sandbox/"

    def test_root_returns_prefix(self, tools):
        assert tools.H.validate_path("/", "/test/sandbox") == "/test/sandbox/"

    def test_double_slashes_normalize(self, tools):
        assert (
            tools.H.validate_path("foo//bar", "/test/sandbox")
            == "/test/sandbox/foo/bar"
        )

    def test_dot_slash_resolves(self, tools):
        assert (
            tools.H.validate_path("foo/./bar", "/test/sandbox")
            == "/test/sandbox/foo/bar"
        )

    def test_multiple_dots_slash_resolves_blocked(self, tools):
        # Note: Current implementation blocks "foo/../bar" because ".." is checked
        # before normalization. This is intentional for security.
        with pytest.raises(Exception, match="traversal not allowed"):
            tools.H.validate_path("foo/../bar", "/test/sandbox")

    def test_consecutive_dots_blocked(self, tools):
        # ".." in path is blocked before normalization for security
        with pytest.raises(Exception, match="traversal not allowed"):
            tools.H.validate_path("foo/../../bar", "/test/sandbox")

    def test_path_with_trailing_whitespace(self, tools):
        assert tools.H.validate_path("foo  ", "/test/sandbox") == "/test/sandbox/foo"


class TestValidatePathTraversalPrevention:
    """Test that path traversal attacks are blocked"""

    def test_double_dot_raises(self, tools):
        with pytest.raises(Exception, match="traversal not allowed"):
            tools.H.validate_path("../../etc/passwd", "/test/sandbox")

    def test_dot_dot_slash_raises(self, tools):
        with pytest.raises(Exception, match="traversal not allowed"):
            tools.H.validate_path("/../etc/passwd", "/test/sandbox")

    def test_trailing_double_dot_raises(self, tools):
        with pytest.raises(Exception, match="traversal not allowed"):
            tools.H.validate_path("foo/../../", "/test/sandbox")

    def test_deep_traversal_raises(self, tools):
        with pytest.raises(Exception, match="traversal not allowed"):
            tools.H.validate_path("a/b/c/../../../../../etc/passwd", "/test/sandbox")

    def test_traversal_with_encoded_slash_returns_valid_path(self, tools):
        # %2F is URL-encoded slash, doesn't contain ".."
        assert (
            tools.H.validate_path("foo%2F%2Fbar", "/test/sandbox")
            == "/test/sandbox/foo/bar"
        )


class TestValidatePathSandboxBoundary:
    """Test that paths outside sandbox are rejected"""

    def test_parent_dir_outside_sandbox_blocked_by_traversal_check(self, tools):
        # Parent directory traversal blocked by ".." check first
        with pytest.raises(Exception, match="traversal not allowed"):
            tools.H.validate_path("../../../etc/passwd", "/test/sandbox")

    def test_sibling_dir_outside_sandbox_blocked_by_traversal_check(self, tools):
        # Sibling dir traversal blocked by ".." check
        with pytest.raises(Exception, match="traversal not allowed"):
            tools.H.validate_path("../other", "/test/sandbox")

    def test_same_level_outside_sandbox_blocked_by_traversal_check(self, tools):
        # Same level traversal blocked by ".." check
        with pytest.raises(Exception, match="traversal not allowed"):
            tools.H.validate_path("test_dir/../../other", "/test/sandbox")


class TestValidatePathUrlEncoding:
    """Test URL encoding is properly decoded before validation"""

    def test_encoded_slash_decodes(self, tools):
        assert (
            tools.H.validate_path("foo%2Fbar", "/test/sandbox")
            == "/test/sandbox/foo/bar"
        )

    def test_encoded_percent_decodes(self, tools):
        # %252F decodes to %2F, then normpath resolves to /
        assert (
            tools.H.validate_path("foo%252Fbar", "/test/sandbox")
            == "/test/sandbox/foo/bar"
        )

    def test_encoded_dot_does_not_bypass_traversal(self, tools):
        with pytest.raises(Exception, match="traversal not allowed"):
            tools.H.validate_path("%2e%2e%2fetc", "/test/sandbox")

    def test_encoded_space_in_path(self, tools):
        assert (
            tools.H.validate_path("foo%20bar", "/test/sandbox")
            == "/test/sandbox/foo bar"
        )

    def test_encoded_ampersand_in_path(self, tools):
        assert (
            tools.H.validate_path("foo%26bar", "/test/sandbox")
            == "/test/sandbox/foo&bar"
        )


class TestValidatePathComplexScenarios:
    """Test complex path combinations"""

    def test_nested_path_with_normalization_blocked(self, tools):
        # Contains ".." so blocked before normalization
        with pytest.raises(Exception, match="traversal not allowed"):
            tools.H.validate_path("a/b/c/../d/e/./f", "/test/sandbox")

    def test_mixed_normalization_and_traversal_blocked(self, tools):
        # Contains ".." so blocked before normalization
        with pytest.raises(Exception, match="traversal not allowed"):
            tools.H.validate_path("a/b/c/../../d", "/test/sandbox")

    def test_multiple_encoded_segments(self, tools):
        assert (
            tools.H.validate_path("a%2Fb%2Fc", "/test/sandbox") == "/test/sandbox/a/b/c"
        )

    def test_path_with_special_characters(self, tools):
        assert (
            tools.H.validate_path("foo-bar_baz.txt", "/test/sandbox")
            == "/test/sandbox/foo-bar_baz.txt"
        )

    def test_long_nested_path(self, tools):
        assert (
            tools.H.validate_path("a/b/c/d/e/f/g", "/test/sandbox")
            == "/test/sandbox/a/b/c/d/e/f/g"
        )

    def test_path_at_sandbox_root(self, tools):
        assert (
            tools.H.validate_path("sandbox", "/test/sandbox") == "/test/sandbox/sandbox"
        )

    def test_path_with_multiple_trailing_slashes(self, tools):
        # normpath removes trailing slashes
        assert tools.H.validate_path("foo///", "/test/sandbox") == "/test/sandbox/foo"

    def test_path_with_leading_and_trailing_whitespace(self, tools):
        assert (
            tools.H.validate_path("  foo/bar  ", "/test/sandbox")
            == "/test/sandbox/foo/bar"
        )
