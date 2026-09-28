"""
Helper function tests
Tests sandbox security, path traversal prevention, and normalization
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from owuinc.owuinc import (
    Tools,
    is_blacklisted,
    is_whitelisted,
    parse_reminders,
    validate_path,
)


@pytest.fixture
def valves():
    """Create a mock valves object with test sandbox"""
    from pydantic import BaseModel

    class MockValves(BaseModel):
        SANDBOX_DIR: str = "test/sandbox"

    return MockValves()


class TestValidatePathSanitization:
    """Test path normalization and whitespace handling"""

    def test_empty_path_returns_prefix(self, valves):
        assert validate_path("", valves) == "test/sandbox/"

    def test_whitespace_only_path_strips_to_empty(self, valves):
        assert validate_path("   ", valves) == "test/sandbox/"

    def test_path_with_trailing_slash(self, valves):
        assert validate_path("foo/", valves) == "test/sandbox/foo"

    def test_path_with_leading_slash(self, valves):
        # Leading slash is stripped and treated as relative to sandbox
        assert validate_path("/foo", valves) == "test/sandbox/foo"

    def test_dot_returns_prefix(self, valves):
        assert validate_path(".", valves) == "test/sandbox/"

    def test_root_returns_prefix(self, valves):
        assert validate_path("/", valves) == "test/sandbox/"

    def test_double_slashes_normalize(self, valves):
        assert validate_path("foo//bar", valves) == "test/sandbox/foo/bar"

    def test_dot_slash_resolves(self, valves):
        assert validate_path("foo/./bar", valves) == "test/sandbox/foo/bar"

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
        assert validate_path("foo  ", valves) == "test/sandbox/foo"


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
        assert validate_path("foo%2F%2Fbar", valves) == "test/sandbox/foo/bar"


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
        assert validate_path("foo%2Fbar", valves) == "test/sandbox/foo/bar"

    def test_encoded_percent_decodes(self, valves):
        # %252F decodes to %2F, then normpath resolves to /
        assert validate_path("foo%252Fbar", valves) == "test/sandbox/foo/bar"

    def test_encoded_dot_does_not_bypass_traversal(self, valves):
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path("%2e%2e%2fetc", valves)

    def test_encoded_space_in_path(self, valves):
        assert validate_path("foo%20bar", valves) == "test/sandbox/foo bar"

    def test_encoded_ampersand_in_path(self, valves):
        assert validate_path("foo%26bar", valves) == "test/sandbox/foo&bar"


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
        assert validate_path("a%2Fb%2Fc", valves) == "test/sandbox/a/b/c"

    def test_path_with_special_characters(self, valves):
        assert (
            validate_path("foo-bar_baz.txt", valves) == "test/sandbox/foo-bar_baz.txt"
        )

    def test_long_nested_path(self, valves):
        assert validate_path("a/b/c/d/e/f/g", valves) == "test/sandbox/a/b/c/d/e/f/g"

    def test_path_at_sandbox_root(self, valves):
        assert validate_path("sandbox", valves) == "test/sandbox/sandbox"

    def test_path_with_multiple_trailing_slashes(self, valves):
        # normpath removes trailing slashes
        assert validate_path("foo///", valves) == "test/sandbox/foo"

    def test_path_with_leading_and_trailing_whitespace(self, valves):
        assert validate_path("  foo/bar  ", valves) == "test/sandbox/foo/bar"


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
        # /etc/passwd should map to test/sandbox/etc/passwd, not system root
        assert validate_path("/etc/passwd", valves) == "test/sandbox/etc/passwd"

    def test_root_slash_returns_sandbox_root(self, valves):
        # "/" maps to sandbox root
        assert validate_path("/", valves) == "test/sandbox/"

    def test_deep_absolute_path_strips_leading_slash(self, valves):
        # /var/log/syslog should map to test/sandbox/var/log/syslog
        assert validate_path("/var/log/syslog", valves) == "test/sandbox/var/log/syslog"

    def test_nested_absolute_path_strips_leading_slash(self, valves):
        # /Documents/src/main.py should map to test/sandbox/Documents/src/main.py
        assert (
            validate_path("/Documents/src/main.py", valves)
            == "test/sandbox/Documents/src/main.py"
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

    def test_only_dot_dots_allowed(self, valves):
        """'...' is a legal name; only '..' segments are traversal."""
        assert validate_path(".../file", valves) == "test/sandbox/.../file"

    def test_four_dots_allowed(self, valves):
        """A file literally named '....' is legal."""
        assert validate_path("....", valves) == "test/sandbox/...."


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


class TestFormatSize:
    """Test _format_size helper"""

    @pytest.fixture
    def tools(self):
        return Tools()

    def test_zero_bytes(self, tools):
        assert tools._format_size("0") == "0 B"

    def test_small_bytes(self, tools):
        assert tools._format_size("42") == "42 B"

    def test_exactly_1023_bytes(self, tools):
        assert tools._format_size("1023") == "1023 B"

    def test_kilobytes(self, tools):
        assert tools._format_size("1024") == "1.0 KB"

    def test_large_kilobytes(self, tools):
        assert tools._format_size("2048") == "2.0 KB"

    def test_megabytes(self, tools):
        assert tools._format_size("1048576") == "1.0 MB"

    def test_gigabytes(self, tools):
        assert tools._format_size("1073741824") == "1.0 GB"

    def test_terabytes(self, tools):
        assert tools._format_size("1099511627776") == "1.0 TB"

    def test_petabytes(self, tools):
        assert tools._format_size("1125899906842624") == "1.0 PB"

    def test_invalid_input(self, tools):
        assert tools._format_size("abc") == "abc"

    def test_none_input(self, tools):
        assert tools._format_size(None) == "n/a"  # type: ignore

    def test_empty_size_is_na(self, tools):
        # Collections carry no getcontentlength; the server returns "".
        assert tools._format_size("") == "n/a"

    def test_whitespace_size_is_na(self, tools):
        assert tools._format_size("   ") == "n/a"


class TestFormatDatetime:
    """Test _format_datetime helper"""

    @pytest.fixture
    def tools(self):
        return Tools()

    def test_empty_string(self, tools):
        assert tools._format_datetime("") == "n/a"

    def test_none_input(self, tools):
        assert tools._format_datetime(None) == "n/a"  # type: ignore

    def test_iso_format_with_z(self, tools):
        result = tools._format_datetime("2026-06-22T10:30:00Z")
        assert "2026-06-22" in result

    def test_iso_format_with_offset(self, tools):
        result = tools._format_datetime("2026-06-22T10:30:00+00:00")
        assert "2026-06-22" in result

    def test_invalid_format_returns_original(self, tools):
        result = tools._format_datetime("not-a-date")
        assert result == "not-a-date"


class TestParseReminders:
    """Test parse_reminders helper"""

    def test_none_returns_empty(self):
        assert parse_reminders(None) == []

    def test_empty_list_returns_empty(self):
        assert parse_reminders([]) == []

    def test_zero_variants(self):
        for r in ["0", "0min", "0 min"]:
            result = parse_reminders([r])
            assert result == [{"minutes": 0, "action": "DISPLAY"}]

    def test_minutes(self):
        result = parse_reminders(["15min"])
        assert result == [{"minutes": 15, "action": "DISPLAY"}]

    def test_minutes_variants(self):
        for r in ["15min", "15mins", "15minutes"]:
            result = parse_reminders([r])
            assert result == [{"minutes": 15, "action": "DISPLAY"}]

    def test_hours(self):
        result = parse_reminders(["2h"])
        assert result == [{"minutes": 120, "action": "DISPLAY"}]

    def test_hours_variants(self):
        for r in ["1h", "1hr", "1hour", "1hours"]:
            result = parse_reminders([r])
            assert result == [{"minutes": 60, "action": "DISPLAY"}]

    def test_days(self):
        result = parse_reminders(["3d"])
        assert result == [{"minutes": 4320, "action": "DISPLAY"}]

    def test_days_variants(self):
        for r in ["1d", "1day", "1days"]:
            result = parse_reminders([r])
            assert result == [{"minutes": 1440, "action": "DISPLAY"}]

    def test_unrecognized_raises_error(self):
        import pytest

        with pytest.raises(ValueError, match="unrecognized reminder format"):
            parse_reminders(["now"])

    def test_multiple_reminders(self):
        result = parse_reminders(["0min", "15min", "1h"])
        assert len(result) == 3
        assert result[0] == {"minutes": 0, "action": "DISPLAY"}
        assert result[1] == {"minutes": 15, "action": "DISPLAY"}
        assert result[2] == {"minutes": 60, "action": "DISPLAY"}


class TestParseRrule:
    """Test _parse_rrule validation and round-trip serialization"""

    def test_valid_weekly_rule(self):
        from owuinc.owuinc import _parse_rrule

        parsed = _parse_rrule("FREQ=WEEKLY;BYDAY=MO")
        assert parsed["FREQ"] in ("WEEKLY", ["WEEKLY"])

    def test_strips_rrule_prefix(self):
        from owuinc.owuinc import _parse_rrule

        parsed = _parse_rrule("RRULE:FREQ=DAILY;COUNT=3")
        assert parsed["FREQ"] in ("DAILY", ["DAILY"])

    def test_missing_freq_raises(self):
        import pytest

        from owuinc.owuinc import _parse_rrule

        with pytest.raises(ValueError, match="FREQ"):
            _parse_rrule("BYDAY=MO")

    def test_garbage_freq_raises(self):
        import pytest

        from owuinc.owuinc import _parse_rrule

        with pytest.raises(ValueError):
            _parse_rrule("FREQ=WEEKLYLY")

    def test_empty_raises(self):
        import pytest

        from owuinc.owuinc import _parse_rrule

        with pytest.raises(ValueError, match="empty RRULE"):
            _parse_rrule("   ")

    def test_round_trip_serializes_unescaped(self):
        """Parsed vRecur serializes via add() without escaped separators."""
        from icalendar import Event

        from owuinc.owuinc import _parse_rrule

        e = Event()
        e.add("rrule", _parse_rrule("FREQ=WEEKLY;BYDAY=MO"))
        ical = e.to_ical().decode()
        assert "RRULE:FREQ=WEEKLY;BYDAY=MO" in ical
        assert "\\;" not in ical


class TestValidatePathEmptySandbox:
    """Test validate_path with empty SANDBOX_DIR (no sandbox confinement)."""

    def test_empty_sandbox_path_returns_path(self):
        from pydantic import BaseModel

        class MockValves(BaseModel):
            SANDBOX_DIR: str = ""

        valves = MockValves()
        result = validate_path("file.txt", valves)
        assert result == "/file.txt"

    def test_empty_sandbox_nested_path(self):
        from pydantic import BaseModel

        class MockValves(BaseModel):
            SANDBOX_DIR: str = ""

        valves = MockValves()
        result = validate_path("a/b/c.txt", valves)
        assert result == "/a/b/c.txt"

    def test_empty_sandbox_traversal_still_blocked(self):
        from pydantic import BaseModel

        class MockValves(BaseModel):
            SANDBOX_DIR: str = ""

        valves = MockValves()
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path("../etc/passwd", valves)

    def test_empty_sandbox_root_returns_slash(self):
        from pydantic import BaseModel

        class MockValves(BaseModel):
            SANDBOX_DIR: str = ""

        valves = MockValves()
        assert validate_path("", valves) == "/"
        assert validate_path(".", valves) == "/"
        assert validate_path("/", valves) == "/"


class TestNormalizeSandboxDir:
    """_normalize_sandbox_dir collapses root-equivalent valve values to ''."""

    def test_root_equivalent_values(self):
        from owuinc.owuinc import _normalize_sandbox_dir

        for raw in ("", "  ", ".", " . ", "/", "./", "/./", "/.", "//"):
            assert _normalize_sandbox_dir(raw) == "", raw

    def test_none_is_root(self):
        from owuinc.owuinc import _normalize_sandbox_dir

        assert _normalize_sandbox_dir(None) == ""

    def test_regular_dirs_unchanged(self):
        from owuinc.owuinc import _normalize_sandbox_dir

        assert _normalize_sandbox_dir("owuinc") == "owuinc"
        assert _normalize_sandbox_dir("owuinc/") == "owuinc"
        assert _normalize_sandbox_dir("/owuinc") == "owuinc"
        assert _normalize_sandbox_dir("  owuinc  ") == "owuinc"

    def test_dot_segments_collapsed(self):
        from owuinc.owuinc import _normalize_sandbox_dir

        assert _normalize_sandbox_dir("./owuinc") == "owuinc"
        assert _normalize_sandbox_dir("owuinc/./sub") == "owuinc/sub"

    def test_dotfile_names_not_root(self):
        from owuinc.owuinc import _normalize_sandbox_dir

        # Dots inside a name are not the root; only a bare '.' is.
        assert _normalize_sandbox_dir("...") == "..."
        assert _normalize_sandbox_dir("..hidden") == "..hidden"


class TestValidatePathDotSandbox:
    """A SANDBOX_DIR of '.' (the UI-reachable root selector) behaves
    exactly like an empty SANDBOX_DIR: clean root paths, traversal blocked."""

    def _valves(self, sandbox: str):
        from pydantic import BaseModel

        class MockValves(BaseModel):
            SANDBOX_DIR: str = sandbox

        return MockValves()

    def test_dot_sandbox_path_returns_root_path(self):
        result = validate_path("file.txt", self._valves("."))
        assert result == "/file.txt"

    def test_dot_sandbox_nested_path(self):
        result = validate_path("a/b/c.txt", self._valves("."))
        assert result == "/a/b/c.txt"

    def test_dot_sandbox_no_dot_segment_in_prefix(self):
        # Regression: '.' must not leak into the URL as a './' segment.
        assert validate_path("file.txt", self._valves(".")) == "/file.txt"
        assert validate_path("", self._valves(".")) == "/"

    def test_dot_sandbox_root_forms(self):
        valves = self._valves(".")
        assert validate_path("", valves) == "/"
        assert validate_path(".", valves) == "/"
        assert validate_path("/", valves) == "/"

    def test_dot_sandbox_traversal_still_blocked(self):
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path("../etc/passwd", self._valves("."))

    def test_slash_sandbox_same_as_empty(self):
        # '/' is another root-equivalent spelling (e.g. saved via API).
        assert validate_path("file.txt", self._valves("/")) == "/file.txt"


# ============================================================
# _resolve_timezone
# ============================================================
class TestResolveTimezone:
    def test_user_timezone_used(self):
        from owuinc.owuinc import _resolve_timezone

        assert str(_resolve_timezone({"timezone": "Europe/Berlin"})) == "Europe/Berlin"

    def test_none_user_falls_back(self):
        from owuinc.owuinc import _resolve_timezone

        assert str(_resolve_timezone(None, "America/New_York")) == "America/New_York"

    def test_missing_key_falls_back(self):
        from owuinc.owuinc import _resolve_timezone

        assert str(_resolve_timezone({}, "Europe/Paris")) == "Europe/Paris"

    def test_empty_timezone_falls_back(self):
        from owuinc.owuinc import _resolve_timezone

        assert (
            str(_resolve_timezone({"timezone": ""}, "Europe/Paris")) == "Europe/Paris"
        )

    def test_invalid_timezone_falls_back_utc(self):
        from owuinc.owuinc import _resolve_timezone

        assert str(_resolve_timezone({"timezone": "Mars/Olympus"})) == "UTC"


# ============================================================
# _get_parent_uid
# ============================================================
class TestGetParentUid:
    @staticmethod
    def _todo(props: str):
        from icalendar import Calendar

        cal = Calendar.from_ical(
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\n"
            "BEGIN:VTODO\r\nUID:me\r\nSUMMARY:x\r\n"
            + props
            + "END:VTODO\r\nEND:VCALENDAR\r\n"
        )
        return cal.walk("VTODO")[0]

    def test_no_related_to_returns_none(self):
        from owuinc.owuinc import _get_parent_uid

        assert _get_parent_uid(self._todo("")) is None

    def test_missing_reltype_means_parent(self):
        from owuinc.owuinc import _get_parent_uid

        comp = self._todo("RELATED-TO:parent1\r\n")
        assert _get_parent_uid(comp) == "parent1"

    def test_explicit_parent_reltype(self):
        from owuinc.owuinc import _get_parent_uid

        comp = self._todo("RELATED-TO;RELTYPE=PARENT:parent2\r\n")
        assert _get_parent_uid(comp) == "parent2"

    def test_child_reltype_ignored(self):
        from owuinc.owuinc import _get_parent_uid

        comp = self._todo("RELATED-TO;RELTYPE=CHILD:other\r\n")
        assert _get_parent_uid(comp) is None

    def test_parent_found_after_child_reverse_relation(self):
        from owuinc.owuinc import _get_parent_uid

        comp = self._todo(
            "RELATED-TO;RELTYPE=CHILD:caldav-xyz\r\n"
            "RELATED-TO;RELTYPE=PARENT:real-parent\r\n"
        )
        assert _get_parent_uid(comp) == "real-parent"

    def test_folded_long_uid_not_truncated(self):
        """Regex-based extraction truncated folded (75-octet wrapped) UIDs."""
        from owuinc.owuinc import _get_parent_uid

        uid = "a" * 70 + "-" + "b" * 20
        folded = f"RELATED-TO;RELTYPE=PARENT:{uid[:70]}\r\n {uid[70:]}\r\n"
        assert _get_parent_uid(self._todo(folded)) == uid


# ============================================================
# _to_aware
# ============================================================
class TestToAware:
    def test_date_becomes_midnight_aware(self):
        from datetime import date
        from zoneinfo import ZoneInfo

        from owuinc.owuinc import _to_aware

        result = _to_aware(date(2026, 9, 1), ZoneInfo("UTC"))
        assert result.hour == 0 and result.tzinfo is not None

    def test_naive_datetime_gets_tz(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from owuinc.owuinc import _to_aware

        result = _to_aware(datetime(2026, 9, 1, 9, 0), ZoneInfo("UTC"))
        assert result.tzinfo is not None

    def test_aware_datetime_unchanged(self):
        from datetime import datetime, timezone

        from owuinc.owuinc import _to_aware

        dt = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
        assert _to_aware(dt, timezone.utc) == dt


# ============================================================
# _expand_occurrences
# ============================================================
class TestExpandOccurrences:
    BASE = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)

    def test_daily_count_within_window(self):
        from owuinc.owuinc import _expand_occurrences

        res = _expand_occurrences(
            "FREQ=DAILY;COUNT=5", self.BASE, self.BASE, self.BASE + timedelta(days=10)
        )
        assert [s for s, _ in res] == [self.BASE + timedelta(days=i) for i in range(5)]
        assert all(r is None for _, r in res)

    def test_occurrence_at_window_start_included(self):
        """Old code used rrule.after(now, inc=False): an event starting
        exactly now was skipped."""
        from owuinc.owuinc import _expand_occurrences

        res = _expand_occurrences(
            "FREQ=DAILY", self.BASE, self.BASE, self.BASE + timedelta(days=2)
        )
        assert res[0][0] == self.BASE

    def test_no_occurrences_outside_window(self):
        from owuinc.owuinc import _expand_occurrences

        res = _expand_occurrences(
            "FREQ=WEEKLY", self.BASE, self.BASE, self.BASE + timedelta(days=3)
        )
        assert len(res) == 1

    def test_exdate_removes_instance(self):
        from owuinc.owuinc import _expand_occurrences

        res = _expand_occurrences(
            "FREQ=DAILY;COUNT=3",
            self.BASE,
            self.BASE,
            self.BASE + timedelta(days=10),
            exdates=[self.BASE + timedelta(days=1)],
        )
        assert [s for s, _ in res] == [self.BASE, self.BASE + timedelta(days=2)]

    def test_override_replaces_instance(self):
        from owuinc.owuinc import _expand_occurrences

        moved = self.BASE + timedelta(hours=3)
        res = _expand_occurrences(
            "FREQ=DAILY;COUNT=2",
            self.BASE,
            self.BASE,
            self.BASE + timedelta(days=10),
            overrides={self.BASE: moved},
        )
        assert res[0] == (moved, self.BASE)
        assert res[1][0] == self.BASE + timedelta(days=1)

    def test_cancelled_override_drops_instance(self):
        from owuinc.owuinc import _expand_occurrences

        res = _expand_occurrences(
            "FREQ=DAILY;COUNT=2",
            self.BASE,
            self.BASE,
            self.BASE + timedelta(days=10),
            overrides={self.BASE: None},
        )
        assert [s for s, _ in res] == [self.BASE + timedelta(days=1)]

    def test_override_into_window_from_outside(self):
        from owuinc.owuinc import _expand_occurrences

        res = _expand_occurrences(
            "FREQ=DAILY",
            self.BASE,
            self.BASE + timedelta(hours=1),
            self.BASE + timedelta(hours=3),
            overrides={self.BASE: self.BASE + timedelta(hours=2)},
        )
        assert res == [(self.BASE + timedelta(hours=2), self.BASE)]

    def test_override_rescheduled_out_of_window_dropped(self):
        """An override moving an in-window instance beyond the window end
        must not leak the new start into the result (the first loop bounds
        the original start, not the rescheduled one)."""
        from owuinc.owuinc import _expand_occurrences

        far = self.BASE + timedelta(days=19)
        res = _expand_occurrences(
            "FREQ=WEEKLY",
            self.BASE,
            self.BASE,
            self.BASE + timedelta(days=3),
            overrides={self.BASE: far},
        )
        assert res == []

    def test_override_rescheduled_before_window_dropped(self):
        from owuinc.owuinc import _expand_occurrences

        early = self.BASE - timedelta(days=19)
        res = _expand_occurrences(
            "FREQ=WEEKLY",
            self.BASE,
            self.BASE,
            self.BASE + timedelta(days=3),
            overrides={self.BASE: early},
        )
        assert res == []


# ============================================================
# _render_recurring_series
# ============================================================
class TestRenderRecurringSeries:
    """Override/EXDATE matching on components shaped like Nextcloud serves
    them: TZID representations that differ from the master's DTSTART,
    floating vs aware values, overrides without DTEND, cancelled instances,
    and all-day series."""

    @staticmethod
    def _ev(**props):
        from icalendar import Event

        e = Event()
        for key, value in props.items():
            # `rid` is shorthand for the RECURRENCE-ID property (not a valid
            # python identifier as "recurrence-id").
            e.add("recurrence-id" if key == "rid" else key, value)
        return e

    @staticmethod
    def _rrule(text: str):
        from icalendar import vRecur

        return vRecur(text)

    @staticmethod
    def _render(comp, siblings, tz_name, base=None):
        from zoneinfo import ZoneInfo

        from owuinc.owuinc import _render_recurring_series

        tz = ZoneInfo(tz_name)
        window_start = datetime(2026, 10, 1, tzinfo=timezone.utc)
        return _render_recurring_series(
            comp,
            siblings,
            tz,
            window_start,
            window_start + timedelta(days=30),
            base or {},
        )

    def test_override_with_foreign_tzid_applies(self):
        """Master in UTC, RECURRENCE-ID in America/Los_Angeles: the override
        must still match its instance by absolute instant."""
        la = ZoneInfo("America/Los_Angeles")
        master = self._ev(
            uid="s1",
            summary="Standup",
            dtstart=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
            dtend=datetime(2026, 10, 1, 13, 0, tzinfo=timezone.utc),
            rrule=self._rrule("FREQ=DAILY;COUNT=4"),
        )
        # 10-02 12:00Z = 05:00 LA; moved to 15:00 LA, no DTEND on override.
        override = self._ev(
            uid="s1",
            summary="Standup (moved)",
            rid=datetime(2026, 10, 2, 5, 0, tzinfo=la),
            dtstart=datetime(2026, 10, 2, 15, 0, tzinfo=la),
        )
        res = self._render(master, [master, override], "UTC")
        assert [datetime.fromisoformat(r["dtstart"]) for r in res] == [
            datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 2, 15, 0, tzinfo=la),
            datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc),
        ]
        moved = res[1]
        assert moved["summary"] == "Standup (moved)"
        # No DTEND on the override: the master's 1h duration applies.
        assert datetime.fromisoformat(moved["dtend"]) == datetime(
            2026, 10, 2, 23, 0, tzinfo=timezone.utc
        )

    def test_floating_master_with_aware_override(self):
        """Floating master DTSTART interpreted in the display tz; override
        stored in UTC. 12:00 NY wall = 16:00Z (EDT)."""
        master = self._ev(
            uid="s2",
            summary="Floaty",
            dtstart=datetime(2026, 10, 1, 12, 0),
            rrule=self._rrule("FREQ=DAILY;COUNT=3"),
        )
        override = self._ev(
            uid="s2",
            summary="Floaty (moved)",
            rid=datetime(2026, 10, 2, 16, 0, tzinfo=timezone.utc),
            dtstart=datetime(2026, 10, 2, 18, 0, tzinfo=timezone.utc),
        )
        res = self._render(master, [master, override], "America/New_York")
        assert [datetime.fromisoformat(r["dtstart"]) for r in res] == [
            datetime(2026, 10, 1, 16, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 2, 18, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 3, 16, 0, tzinfo=timezone.utc),
        ]

    def test_exdate_with_foreign_tzid_drops_instance(self):
        la = ZoneInfo("America/Los_Angeles")
        master = self._ev(
            uid="s3",
            summary="Ex",
            dtstart=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
            dtend=datetime(2026, 10, 1, 13, 0, tzinfo=timezone.utc),
            rrule=self._rrule("FREQ=DAILY;COUNT=4"),
            # 12:00Z expressed in LA wall time
            exdate=[datetime(2026, 10, 2, 5, 0, tzinfo=la)],
        )
        res = self._render(master, [master], "UTC")
        assert sorted(datetime.fromisoformat(r["dtstart"]) for r in res) == [
            datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc),
        ]

    def test_cancelled_instance_with_foreign_tzid(self):
        la = ZoneInfo("America/Los_Angeles")
        master = self._ev(
            uid="s4",
            summary="Ex",
            dtstart=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
            dtend=datetime(2026, 10, 1, 13, 0, tzinfo=timezone.utc),
            rrule=self._rrule("FREQ=DAILY;COUNT=3"),
        )
        override = self._ev(
            uid="s4",
            summary="Ex",
            rid=datetime(2026, 10, 3, 5, 0, tzinfo=la),
            dtstart=datetime(2026, 10, 3, 5, 0, tzinfo=la),
            status="CANCELLED",
        )
        res = self._render(master, [master, override], "UTC")
        assert [datetime.fromisoformat(r["dtstart"]) for r in res] == [
            datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc),
        ]

    def test_all_day_series_with_exdate_and_override(self):
        from datetime import date

        master = self._ev(
            uid="s5",
            summary="AllDay",
            dtstart=date(2026, 10, 1),
            rrule=self._rrule("FREQ=WEEKLY;COUNT=4"),
            exdate=[date(2026, 10, 8)],
        )
        override = self._ev(
            uid="s5",
            summary="AllDay (moved)",
            rid=date(2026, 10, 15),
            dtstart=date(2026, 10, 20),
        )
        res = self._render(
            master, [master, override], "UTC", base={"summary": "AllDay"}
        )
        assert [datetime.fromisoformat(r["dtstart"]) for r in res] == [
            datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 22, 0, 0, tzinfo=timezone.utc),
        ]
        assert res[1]["summary"] == "AllDay (moved)"
        assert all(r["summary"] == "AllDay" for r in res if r is not res[1])

    def test_override_dtend_resets_duration(self):
        master = self._ev(
            uid="s6",
            summary="Long",
            dtstart=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
            dtend=datetime(2026, 10, 1, 13, 0, tzinfo=timezone.utc),
            rrule=self._rrule("FREQ=DAILY;COUNT=2"),
        )
        override = self._ev(
            uid="s6",
            summary="Long",
            rid=datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc),
            dtstart=datetime(2026, 10, 2, 14, 0, tzinfo=timezone.utc),
            dtend=datetime(2026, 10, 2, 16, 0, tzinfo=timezone.utc),
        )
        res = self._render(master, [master, override], "UTC")
        assert datetime.fromisoformat(res[1]["dtend"]) == datetime(
            2026, 10, 2, 16, 0, tzinfo=timezone.utc
        )


# ============================================================
# _check_redos_risk
# ============================================================
class TestCheckRedosRisk:
    def test_nested_quantifier_raises(self):
        from owuinc.owuinc import _check_redos_risk

        with pytest.raises(ValueError, match="nested quantifiers"):
            _check_redos_risk("(a+)+b")

    def test_plain_pattern_passes(self):
        from owuinc.owuinc import _check_redos_risk

        _check_redos_risk(r"ERROR: \w+")

    def test_pattern_with_literal_space_quantifier_passes(self):
        """re.compile('( )+') is valid; parsing with re.VERBOSE would
        strip the space and fail with 'nothing to repeat' — validating a
        different pattern than the one that runs."""
        import re

        from owuinc.owuinc import _check_redos_risk

        re.compile("( )+")  # must be a valid runnable pattern
        _check_redos_risk("( )+")

    def test_hash_pattern_not_treated_as_comment(self):
        from owuinc.owuinc import _check_redos_risk

        _check_redos_risk("a+b#c")

    def test_import_fallback_uses_length_cap(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name.startswith("re._"):
                raise ImportError
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        from owuinc.owuinc import _check_redos_risk

        _check_redos_risk("(a+)+")  # analysis degraded: short pattern allowed
        with pytest.raises(ValueError, match="too long"):
            _check_redos_risk("a" * 501)


class TestCheckNotSandboxRoot:
    def _tools(self, sandbox_dir):
        t = Tools()
        t.valves.SANDBOX_DIR = sandbox_dir
        return t

    def test_sandbox_root_denied(self):
        t = self._tools("owuinc")
        with pytest.raises(ValueError, match="sandbox root"):
            t._check_not_sandbox_root("owuinc/")

    def test_sandbox_root_without_slash_denied(self):
        t = self._tools("owuinc")
        with pytest.raises(ValueError, match="sandbox root"):
            t._check_not_sandbox_root("/owuinc")

    def test_child_path_allowed(self):
        t = self._tools("owuinc")
        t._check_not_sandbox_root("owuinc/notes.md")

    def test_empty_sandbox_denies_account_root(self):
        t = self._tools("")
        with pytest.raises(ValueError, match="sandbox root"):
            t._check_not_sandbox_root("/")
        t._check_not_sandbox_root("notes.md")


class TestCheckReadOnly:
    def _tools(self, read_only):
        t = Tools()
        t.valves.READ_ONLY_PATHS = read_only
        return t

    def test_default_allows_all(self):
        t = Tools()
        for name in ("AGENTS.md", "SOUL.md", "IDENTITY.md", "MEMORY.md", "notes.md"):
            t._check_read_only(name)

    def test_only_the_injected_path_itself_is_protected(self):
        t = self._tools("SOUL.md")
        t._check_read_only("archive/SOUL.md")

    def test_custom_list_with_directory(self):
        t = self._tools("locked")
        with pytest.raises(ValueError, match="read-only"):
            t._check_read_only("locked/inner.md")

    def test_empty_valve_allows_all(self):
        t = self._tools("")
        t._check_read_only("SOUL.md")


class _FailingDavClient:
    async def list_with_infos(self, path, recursive=False):
        raise RuntimeError("server exploded")


class _MissingDavClient:
    async def list_with_infos(self, path, recursive=False):
        from aiowebdav2.exceptions import RemoteResourceNotFoundError

        raise RemoteResourceNotFoundError(path=path)


class _StaticDavClient:
    def __init__(self, infos):
        self._infos = infos

    async def list_with_infos(self, path, recursive=False):
        return self._infos


class TestCheckBlacklistedRecursive:
    def _tools(self, blacklist):
        t = Tools()
        t.valves.SANDBOX_DIR = "owuinc"
        t.valves.FILE_BLACKLIST = blacklist
        return t

    async def test_listing_error_fails_closed(self):
        t = self._tools("secret")
        with pytest.raises(ValueError, match="unable to verify protection state"):
            await t._check_blacklisted_recursive(_FailingDavClient(), "owuinc/dir")

    async def test_not_found_propagates(self):
        from aiowebdav2.exceptions import RemoteResourceNotFoundError

        t = self._tools("secret")
        with pytest.raises(RemoteResourceNotFoundError):
            await t._check_blacklisted_recursive(_MissingDavClient(), "owuinc/dir")

    async def test_blacklisted_descendant_denies(self):
        t = self._tools("dir/secret")
        client = _StaticDavClient(
            [
                {"path": "owuinc/dir"},
                {"path": "owuinc/dir/ok.txt"},
                {"path": "owuinc/dir/secret/token.txt"},
            ]
        )
        with pytest.raises(ValueError, match="Access denied"):
            await t._check_blacklisted_recursive(client, "owuinc/dir")

    async def test_clean_tree_allowed(self):
        t = self._tools("secret")
        client = _StaticDavClient(
            [{"path": "owuinc/dir"}, {"path": "owuinc/dir/ok.txt"}]
        )
        await t._check_blacklisted_recursive(client, "owuinc/dir")

    async def test_blacklisted_descendant_denies_full_webdav_href(self):
        # Real servers return full WebDAV hrefs as listing paths, not
        # sandbox-anchored ones. Protection must still fire on them.
        t = self._tools("dir/secret")
        client = _StaticDavClient(
            [
                {"path": "remote.php/dav/files/u/owuinc/dir"},
                {"path": "remote.php/dav/files/u/owuinc/dir/ok.txt"},
                {"path": "remote.php/dav/files/u/owuinc/dir/secret/token.txt"},
            ]
        )
        with pytest.raises(ValueError, match="Access denied"):
            await t._check_blacklisted_recursive(client, "owuinc/dir")

    async def test_clean_tree_allowed_full_webdav_href(self):
        t = self._tools("dir/secret")
        client = _StaticDavClient(
            [
                {"path": "remote.php/dav/files/u/owuinc/dir"},
                {"path": "remote.php/dav/files/u/owuinc/dir/ok.txt"},
            ]
        )
        await t._check_blacklisted_recursive(client, "owuinc/dir")

    async def test_blacklisted_descendant_denies_root_sandbox_full_href(self):
        # Root sandbox ('.'): the files root is the anchor, and a blacklisted
        # descendant anywhere in the account tree must still be denied.
        t = Tools()
        t.valves.SANDBOX_DIR = "."
        t.valves.WEBDAV_USERNAME = "u"
        t.valves.FILE_BLACKLIST = "box/secretdir"
        client = _StaticDavClient(
            [
                {"path": "remote.php/dav/files/u/box"},
                {"path": "remote.php/dav/files/u/box/ok.txt"},
                {"path": "remote.php/dav/files/u/box/secretdir/token.txt"},
            ]
        )
        with pytest.raises(ValueError, match="Access denied"):
            await t._check_blacklisted_recursive(client, "box")

    async def test_empty_blacklist_skips_listing(self):
        t = self._tools("")
        await t._check_blacklisted_recursive(_FailingDavClient(), "owuinc/dir")


class _ROFileClient:
    """is_dir -> False, so the recursive read-only scan must skip listing."""

    def __init__(self):
        self.list_called = False

    async def is_dir(self, path):
        return False

    async def list_files(self, path, recursive=False):
        self.list_called = True
        return []


class _ROStaticClient:
    def __init__(self, is_dir=True, files=None):
        self._is_dir = is_dir
        self._files = files or []

    async def is_dir(self, path):
        return self._is_dir

    async def list_files(self, path, recursive=False):
        return self._files


class _ROFailingClient:
    async def is_dir(self, path):
        return True

    async def list_files(self, path, recursive=False):
        raise RuntimeError("server exploded")


class _ROMissingClient:
    async def is_dir(self, path):
        from aiowebdav2.exceptions import RemoteResourceNotFoundError

        raise RemoteResourceNotFoundError(path=path)


class TestCheckReadOnlyRecursive:
    def _tools(self, read_only):
        t = Tools()
        t.valves.SANDBOX_DIR = "owuinc"
        t.valves.READ_ONLY_PATHS = read_only
        return t

    async def test_protected_target_denies(self):
        t = self._tools("vault/MEMORY.md")
        with pytest.raises(ValueError, match="read-only"):
            await t._check_read_only_recursive(
                _ROStaticClient(), "owuinc/vault/MEMORY.md"
            )

    async def test_protected_descendant_denies(self):
        t = self._tools("vault/MEMORY.md")
        client = _ROStaticClient(
            files=[
                "/owuinc/vault",
                "/owuinc/vault/notes.txt",
                "/owuinc/vault/MEMORY.md",
            ]
        )
        with pytest.raises(ValueError, match="read-only"):
            await t._check_read_only_recursive(client, "owuinc/vault")

    async def test_nested_protected_path_denies(self):
        t = self._tools("vault/sub/inner.txt")
        client = _ROStaticClient(
            files=["/owuinc/vault/sub/", "/owuinc/vault/sub/inner.txt"]
        )
        with pytest.raises(ValueError, match="read-only"):
            await t._check_read_only_recursive(client, "owuinc/vault")

    async def test_clean_tree_allowed(self):
        t = self._tools("vault/MEMORY.md")
        client = _ROStaticClient(files=["/owuinc/vault", "/owuinc/vault/notes.txt"])
        await t._check_read_only_recursive(client, "owuinc/vault")

    async def test_file_target_skips_listing(self):
        t = self._tools("other.md")
        client = _ROFileClient()
        await t._check_read_only_recursive(client, "owuinc/vault/notes.txt")
        assert client.list_called is False

    async def test_listing_error_fails_closed(self):
        t = self._tools("vault/MEMORY.md")
        with pytest.raises(ValueError, match="unable to verify protection state"):
            await t._check_read_only_recursive(_ROFailingClient(), "owuinc/vault")

    async def test_not_found_propagates(self):
        from aiowebdav2.exceptions import RemoteResourceNotFoundError

        t = self._tools("vault/MEMORY.md")
        with pytest.raises(RemoteResourceNotFoundError):
            await t._check_read_only_recursive(_ROMissingClient(), "owuinc/vault")

    async def test_missing_ok_allows_absent_destination(self):
        t = self._tools("vault/MEMORY.md")
        await t._check_read_only_recursive(
            _ROMissingClient(), "owuinc/newname.txt", missing_ok=True
        )

    async def test_missing_ok_still_denies_protected_descendant(self):
        t = self._tools("vault/MEMORY.md")
        client = _ROStaticClient(
            files=[
                "/owuinc/vault",
                "/owuinc/vault/notes.txt",
                "/owuinc/vault/MEMORY.md",
            ]
        )
        with pytest.raises(ValueError, match="read-only"):
            await t._check_read_only_recursive(client, "owuinc/vault", missing_ok=True)

    async def test_missing_ok_fails_closed_on_listing_error(self):
        t = self._tools("vault/MEMORY.md")
        with pytest.raises(ValueError, match="unable to verify protection state"):
            await t._check_read_only_recursive(
                _ROFailingClient(), "owuinc/vault", missing_ok=True
            )

    async def test_empty_read_only_skips_listing(self):
        t = self._tools("")
        await t._check_read_only_recursive(_ROFailingClient(), "owuinc/vault")


class TestParseReminderUnits:
    def test_weeks(self):
        from owuinc.owuinc import parse_reminders

        assert parse_reminders(["2w"])[0]["minutes"] == 20160
        assert parse_reminders(["1week"])[0]["minutes"] == 10080

    def test_non_string_raises_value_error_not_attribute_error(self):
        from owuinc.owuinc import parse_reminders

        with pytest.raises(ValueError):
            parse_reminders([15])


class TestGlobMatch:
    def test_basename_pattern_matches_any_depth(self):
        from owuinc.owuinc import _glob_match

        assert _glob_match("c.py", "*.py")
        assert _glob_match("a/b/c.py", "*.py")

    def test_single_star_does_not_cross_directories(self):
        from owuinc.owuinc import _glob_match

        assert _glob_match("a/b.py", "*/b.py")
        assert not _glob_match("a/b/c.py", "a/*.py")
        assert _glob_match("a/b/c.py", "a/*/c.py")

    def test_double_star_spans_any_depth(self):
        from owuinc.owuinc import _glob_match

        assert _glob_match("docs/deep/a.md", "docs/**/*.md")
        assert _glob_match("docs/a.md", "docs/**/*.md")
        assert _glob_match("a.md", "**/*.md")
        assert _glob_match("x/a.md", "**/*.md")

    def test_plain_slash_pattern_is_depth_exact(self):
        from owuinc.owuinc import _glob_match

        assert _glob_match("docs/a.md", "docs/*.md")
        assert not _glob_match("docs/deep/a.md", "docs/*.md")

    def test_character_class(self):
        from owuinc.owuinc import _glob_match

        assert _glob_match("f1.txt", "f[0-9].txt")
        assert not _glob_match("fx.txt", "f[0-9].txt")
        assert _glob_match("fx.txt", "f[!0-9].txt")


class TestExpandBraces:
    def test_single_group(self):
        from owuinc.owuinc import _expand_braces

        assert sorted(_expand_braces("*.{py,js}")) == ["*.js", "*.py"]

    def test_multiple_groups(self):
        from owuinc.owuinc import _expand_braces

        assert sorted(_expand_braces("{a,b}.{1,2}")) == ["a.1", "a.2", "b.1", "b.2"]

    def test_no_braces_is_identity(self):
        from owuinc.owuinc import _expand_braces

        assert _expand_braces("plain.txt") == ["plain.txt"]
        assert _expand_braces("unclosed{") == ["unclosed{"]


class TestHrefRel:
    def _tools(self):
        from owuinc.owuinc import Tools

        t = Tools()
        t.valves.SANDBOX_DIR = "owuinc"
        return t

    def test_rooted_href_strips_prefix(self):
        t = self._tools()
        assert t._href_rel("remote.php/dav/files/u/owuinc/docs/a.md") == "docs/a.md"

    def test_prefix_only_in_string_is_stripped(self):
        t = self._tools()
        # A root-rooted href that does not start at the prefix: the only
        # sandbox-anchored reading is a mid-string strip.
        assert t._href_rel("//remote.php/dav/files/u/owuinc/a.md") == "a.md"

    def test_trailing_slash_removed(self):
        t = self._tools()
        assert t._href_rel("remote.php/dav/files/u/owuinc/docs/") == "docs"

    def test_prefix_absent_keeps_full_path(self):
        t = self._tools()
        # A sandbox-relative href carrying no prefix: the whole path must
        # survive so blacklist matching on subpaths keeps working (the old
        # basename fallback let 'secrets/creds' slip past a 'secrets' entry).
        assert t._href_rel("secrets/creds") == "secrets/creds"
        assert t._href_rel("docs/a.md") == "docs/a.md"

    def test_plain_name_is_identity(self):
        t = self._tools()
        assert t._href_rel("a.md") == "a.md"

    def _root_tools(self):
        from owuinc.owuinc import Tools

        t = Tools()
        t.valves.SANDBOX_DIR = "."
        t.valves.WEBDAV_USERNAME = "u"
        return t

    def test_root_sandbox_anchors_on_webdav_files_root(self):
        t = self._root_tools()
        # The sandbox is the whole user file root, so the WebDAV files root is
        # the anchor, not the (empty) sandbox prefix.
        assert t._href_rel("remote.php/dav/files/u/docs/a.md") == "docs/a.md"
        assert t._href_rel("/remote.php/dav/files/u/a.md") == "a.md"

    def test_root_sandbox_does_not_strip_a_segment(self):
        t = self._root_tools()
        # Regression: with prefix '/' a naive split on '/' would strip a real
        # segment ('remote.php'). The files-root anchor must be used instead.
        assert (
            t._href_rel("remote.php/dav/files/u/dotbox/inner.txt") == "dotbox/inner.txt"
        )

    def test_root_sandbox_relative_passthrough(self):
        t = self._root_tools()
        assert t._href_rel("dotbox/inner.txt") == "dotbox/inner.txt"
        assert t._href_rel("a.md") == "a.md"
