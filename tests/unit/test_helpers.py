"""
Helper function tests
Tests sandbox security, path traversal prevention, and normalization
"""

import pytest

from owuinc.owuinc import is_blacklisted, is_whitelisted, validate_path


@pytest.fixture
def valves():
    """Create a mock valves object with test sandbox"""
    from pydantic import BaseModel

    class MockValves(BaseModel):
        SANDBOX_DIR: str = "/test/sandbox"

    return MockValves()


class TestValidatePathSanitization:
    """Test path normalization and whitespace handling"""

    def test_empty_path_returns_prefix(self, valves):
        assert validate_path("", valves) == "/test/sandbox/"

    def test_whitespace_only_path_strips_to_empty(self, valves):
        assert validate_path("   ", valves) == "/test/sandbox/"

    def test_path_with_trailing_slash(self, valves):
        assert validate_path("foo/", valves) == "/test/sandbox/foo"

    def test_path_with_leading_slash(self, valves):
        # Leading slash is stripped and treated as relative to sandbox
        assert validate_path("/foo", valves) == "/test/sandbox/foo"

    def test_dot_returns_prefix(self, valves):
        assert validate_path(".", valves) == "/test/sandbox/"

    def test_root_returns_prefix(self, valves):
        assert validate_path("/", valves) == "/test/sandbox/"

    def test_double_slashes_normalize(self, valves):
        assert validate_path("foo//bar", valves) == "/test/sandbox/foo/bar"

    def test_dot_slash_resolves(self, valves):
        assert validate_path("foo/./bar", valves) == "/test/sandbox/foo/bar"

    def test_multiple_dots_slash_resolves_blocked(self, valves):
        # Note: Current implementation blocks "foo/../bar" because ".." is checked
        # before normalization. This is intentional for security.
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path("foo/../bar", valves)

    def test_consecutive_dots_blocked(self, valves):
        # ".." in path is blocked before normalization for security
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path("foo/../../bar", valves)

    def test_path_with_trailing_whitespace(self, valves):
        assert validate_path("foo  ", valves) == "/test/sandbox/foo"


class TestValidatePathTraversalPrevention:
    """Test that path traversal attacks are blocked"""

    def test_double_dot_raises(self, valves):
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path("../../etc/passwd", valves)

    def test_dot_dot_slash_raises(self, valves):
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path("/../etc/passwd", valves)

    def test_trailing_double_dot_raises(self, valves):
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path("foo/../../", valves)

    def test_deep_traversal_raises(self, valves):
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path("a/b/c/../../../../../etc/passwd", valves)

    def test_traversal_with_encoded_slash_returns_valid_path(self, valves):
        # %2F is URL-encoded slash, doesn't contain ".."
        assert validate_path("foo%2F%2Fbar", valves) == "/test/sandbox/foo/bar"


class TestValidatePathSandboxBoundary:
    """Test that paths outside sandbox are rejected"""

    def test_parent_dir_outside_sandbox_blocked_by_traversal_check(self, valves):
        # Parent directory traversal blocked by ".." check first
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path("../../../etc/passwd", valves)

    def test_sibling_dir_outside_sandbox_blocked_by_traversal_check(self, valves):
        # Sibling dir traversal blocked by ".." check
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path("../other", valves)

    def test_same_level_outside_sandbox_blocked_by_traversal_check(self, valves):
        # Same level traversal blocked by ".." check
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path("test_dir/../../other", valves)


class TestValidatePathUrlEncoding:
    """Test URL encoding is properly decoded before validation"""

    def test_encoded_slash_decodes(self, valves):
        assert validate_path("foo%2Fbar", valves) == "/test/sandbox/foo/bar"

    def test_encoded_percent_decodes(self, valves):
        # %252F decodes to %2F, then normpath resolves to /
        assert validate_path("foo%252Fbar", valves) == "/test/sandbox/foo/bar"

    def test_encoded_dot_does_not_bypass_traversal(self, valves):
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path("%2e%2e%2fetc", valves)

    def test_encoded_space_in_path(self, valves):
        assert validate_path("foo%20bar", valves) == "/test/sandbox/foo bar"

    def test_encoded_ampersand_in_path(self, valves):
        assert validate_path("foo%26bar", valves) == "/test/sandbox/foo&bar"


class TestValidatePathComplexScenarios:
    """Test complex path combinations"""

    def test_nested_path_with_normalization_blocked(self, valves):
        # Contains ".." so blocked before normalization
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path("a/b/c/../d/e/./f", valves)

    def test_mixed_normalization_and_traversal_blocked(self, valves):
        # Contains ".." so blocked before normalization
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path("a/b/c/../../d", valves)

    def test_multiple_encoded_segments(self, valves):
        assert validate_path("a%2Fb%2Fc", valves) == "/test/sandbox/a/b/c"

    def test_path_with_special_characters(self, valves):
        assert (
            validate_path("foo-bar_baz.txt", valves) == "/test/sandbox/foo-bar_baz.txt"
        )

    def test_long_nested_path(self, valves):
        assert validate_path("a/b/c/d/e/f/g", valves) == "/test/sandbox/a/b/c/d/e/f/g"

    def test_path_at_sandbox_root(self, valves):
        assert validate_path("sandbox", valves) == "/test/sandbox/sandbox"

    def test_path_with_multiple_trailing_slashes(self, valves):
        # normpath removes trailing slashes
        assert validate_path("foo///", valves) == "/test/sandbox/foo"

    def test_path_with_leading_and_trailing_whitespace(self, valves):
        assert validate_path("  foo/bar  ", valves) == "/test/sandbox/foo/bar"


class TestIsWhitelisted:
    """Test whitelist validation logic"""

    def test_empty_whitelist_returns_false(self):
        assert is_whitelisted("", "cal1") is False

    def test_item_in_whitelist(self):
        assert is_whitelisted("cal1, cal2", "cal1") is True

    def test_item_not_in_whitelist(self):
        assert is_whitelisted("cal1, cal2", "cal3") is False

    def test_whitespace_normalized(self):
        assert is_whitelisted("  cal1  ,  cal2  ", "cal1") is True

    def test_trailing_comma_filtered(self):
        assert is_whitelisted("cal1, cal2,", "cal1") is True

    def test_case_sensitive(self):
        assert is_whitelisted("cal1", "CAL1") is False


class TestValidatePathLeadingSlash:
    """Test that leading slashes are stripped for sandbox confinement"""

    def test_absolute_path_strips_leading_slash(self, valves):
        # /etc/passwd should map to /test/sandbox/etc/passwd, not system root
        assert validate_path("/etc/passwd", valves) == "/test/sandbox/etc/passwd"

    def test_root_slash_returns_sandbox_root(self, valves):
        # "/" maps to sandbox root
        assert validate_path("/", valves) == "/test/sandbox/"

    def test_deep_absolute_path_strips_leading_slash(self, valves):
        # /var/log/syslog should map to /test/sandbox/var/log/syslog
        assert (
            validate_path("/var/log/syslog", valves) == "/test/sandbox/var/log/syslog"
        )

    def test_nested_absolute_path_strips_leading_slash(self, valves):
        # /Documents/src/main.py should map to /test/sandbox/Documents/src/main.py
        assert (
            validate_path("/Documents/src/main.py", valves)
            == "/test/sandbox/Documents/src/main.py"
        )


class TestValidatePathSecurityEdgeCases:
    """Test additional security edge cases"""

    def test_sandbox_dir_without_leading_slash(self):
        """SANDOWN_DIR should not have leading slash per Valve defaults"""
        from pydantic import BaseModel

        class MockValves(BaseModel):
            SANDBOX_DIR: str = "owuinc"

        valves = MockValves()
        assert validate_path("Documents/file.py", valves) == "owuinc/Documents/file.py"

    def test_sandbox_dir_strips_trailing_slash(self):
        """SANDBOX_DIR with trailing slash should be normalized"""
        from pydantic import BaseModel

        class MockValves(BaseModel):
            SANDBOX_DIR: str = "owuinc/"

        valves = MockValves()
        assert validate_path("Documents/file.py", valves) == "owuinc/Documents/file.py"

    def test_only_dot_dots_blocked(self, valves):
        """Multiple consecutive dots should be blocked if they contain .."""
        # ".../file" contains ".." so it's blocked
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path(".../file", valves)

    def test_four_dots_blocked(self, valves):
        """Four dots contain .. and should be blocked"""
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path("....", valves)


class TestIsBlacklisted:
    """Test blacklist prefix matching logic"""

    def test_empty_blacklist_returns_false(self):
        assert is_blacklisted("", "any/path") is False

    def test_exact_match(self):
        assert is_blacklisted("foo", "foo") is True

    def test_prefix_match(self):
        assert is_blacklisted("foo/bar", "foo/bar/baz/file.txt") is True

    def test_prefix_match_deep(self):
        assert is_blacklisted("foo", "foo/a/b/c/deep/file.txt") is True

    def test_no_match_different_prefix(self):
        assert is_blacklisted("foo", "other/file.txt") is False

    def test_no_match_similar_name(self):
        assert is_blacklisted("foo", "foobar/file.txt") is False

    def test_no_match_substring_without_separator(self):
        assert is_blacklisted("foo", "Myfoo/file.txt") is False

    def test_comma_separated_multiple(self):
        assert is_blacklisted("foo, bar", "bar/file.txt") is True

    def test_comma_separated_first_match(self):
        assert is_blacklisted("foo, bar", "foo/file.txt") is True

    def test_comma_separated_no_match(self):
        assert is_blacklisted("foo, bar", "other/file.txt") is False

    def test_whitespace_normalized(self):
        assert is_blacklisted("  foo  ,  bar  ", "foo/file.txt") is True

    def test_trailing_comma_filtered(self):
        assert is_blacklisted("foo,", "foo/file.txt") is True

    def test_single_level_path(self):
        assert is_blacklisted("foo", "foo") is True

    def test_multilevel_blacklist_exact(self):
        assert is_blacklisted("a/b/c", "a/b/c/file.txt") is True

    def test_multilevel_blacklist_exact_match(self):
        assert is_blacklisted("a/b/c", "a/b/c") is True

    def test_multilevel_blacklist_no_partial_match(self):
        assert is_blacklisted("a/b/c", "a/b/file.txt") is False

    def test_leading_slash_stripped_by_caller(self):
        assert is_blacklisted("foo", "foo/file.txt") is True
