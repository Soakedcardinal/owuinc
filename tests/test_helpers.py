"""
Helper function tests for validate_path
Tests sandbox security, path traversal prevention, and normalization
"""

import pytest

from owuinc.owuinc import (
    is_whitelisted,
    validate_calendar,
    validate_path,
    validate_task_list,
)


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
        # Leading slash creates double slash after prefix
        assert validate_path("/foo", valves) == "/test/sandbox//foo"

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


@pytest.fixture
def task_list_valves():
    """Create a mock valves object for task list validation"""
    from pydantic import BaseModel

    class MockValves(BaseModel):
        TASK_LIST_WHITELIST: str = "personal, work, shopping"
        DEFAULT_TASK_LIST: str = "personal"

    return MockValves()


@pytest.fixture
def calendar_valves():
    """Create a mock valves object for calendar validation"""
    from pydantic import BaseModel

    class MockValves(BaseModel):
        CALENDAR_WHITELIST: str = "home, office, main"
        DEFAULT_CALENDAR: str = "home"

    return MockValves()


class TestValidateTaskList:
    """Test validate_task_list function"""

    def test_valid_list_in_whitelist(self, task_list_valves):
        result = validate_task_list(task_list_valves, "personal")
        assert result == "personal"

    def test_valid_list_with_whitespace(self, task_list_valves):
        result = validate_task_list(task_list_valves, "  work  ")
        assert result == "work"

    def test_invalid_list_not_in_whitelist(self, task_list_valves):
        with pytest.raises(Exception, match="not in whitelist"):
            validate_task_list(task_list_valves, "invalid")

    def test_empty_string_uses_default(self, task_list_valves):
        result = validate_task_list(task_list_valves, None)
        assert result == "personal"

    def test_empty_string_default_strip(self, task_list_valves):
        result = validate_task_list(task_list_valves, "")
        assert result == "personal"

    def test_empty_whitelist_raises(self):
        from pydantic import BaseModel

        class MockValves(BaseModel):
            TASK_LIST_WHITELIST: str = ""
            DEFAULT_TASK_LIST: str = "personal"

        valves = MockValves()
        with pytest.raises(Exception, match="whitelist not configured"):
            validate_task_list(valves, "personal")

    def test_none_uses_default(self, task_list_valves):
        result = validate_task_list(task_list_valves, None)
        assert result == "personal"


class TestValidateCalendar:
    """Test validate_calendar function"""

    def test_valid_calendar_in_whitelist(self, calendar_valves):
        result = validate_calendar(calendar_valves, "home")
        assert result == "home"

    def test_valid_calendar_with_whitespace(self, calendar_valves):
        result = validate_calendar(calendar_valves, "  office  ")
        assert result == "office"

    def test_invalid_calendar_not_in_whitelist(self, calendar_valves):
        with pytest.raises(Exception, match="not in whitelist"):
            validate_calendar(calendar_valves, "invalid")

    def test_empty_string_uses_default(self, calendar_valves):
        result = validate_calendar(calendar_valves, None)
        assert result == "home"

    def test_empty_string_default_strip(self, calendar_valves):
        result = validate_calendar(calendar_valves, "")
        assert result == "home"

    def test_empty_whitelist_raises(self):
        from pydantic import BaseModel

        class MockValves(BaseModel):
            CALENDAR_WHITELIST: str = ""
            DEFAULT_CALENDAR: str = "home"

        valves = MockValves()
        with pytest.raises(Exception, match="whitelist not configured"):
            validate_calendar(valves, "home")

    def test_none_uses_default(self, calendar_valves):
        result = validate_calendar(calendar_valves, None)
        assert result == "home"
