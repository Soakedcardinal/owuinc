"""
Helper function tests
Tests sandbox security, path traversal prevention, and normalization
"""

from datetime import datetime, timedelta, timezone

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
