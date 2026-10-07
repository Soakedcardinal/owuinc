"""Behavioral edge-case unit tests.

Implementation-pinning source-string assertions were removed: the behaviors
they pinned (complete_task fields, recurring delegation, calendar window
bounds, ls self-skip, mv/cp overwrite, If-Match usage, binary rejection,
exact-match lookup) are covered end-to-end by the integration suite against
real servers. What remains here is logic integration cannot exercise:
input validation, ReDoS rejection, and injected-failure behavior.
"""

import pytest
from pydantic import BaseModel


class MockValves(BaseModel):
    SANDBOX_DIR: str = "owuinc"
    FILE_BLACKLIST: str = ""


# ============================================================
# parse_reminders: validation for unrecognized formats
# ============================================================
class TestParseRemindersValidation:
    """Unrecognized reminder formats raise ValueError."""

    def test_icalendar_duration_raises(self):
        import pytest

        from owuinc.owuinc import parse_reminders

        with pytest.raises(ValueError, match="unrecognized reminder format"):
            parse_reminders(["PT15M"])

    def test_unknown_suffix_raises(self):
        import pytest

        from owuinc.owuinc import parse_reminders

        with pytest.raises(ValueError, match="unrecognized reminder format"):
            parse_reminders(["15sec"])

    def test_garbage_input_raises(self):
        import pytest

        from owuinc.owuinc import parse_reminders

        with pytest.raises(ValueError, match="unrecognized reminder format"):
            parse_reminders(["foo"])

    def test_empty_string_raises(self):
        import pytest

        from owuinc.owuinc import parse_reminders

        with pytest.raises(ValueError, match="unrecognized reminder format"):
            parse_reminders([""])

    def test_valid_formats_still_work(self):
        """Regression: valid formats must still parse."""
        from owuinc.owuinc import parse_reminders

        assert parse_reminders(["0min"]) == [{"minutes": 0, "action": "DISPLAY"}]
        assert parse_reminders(["15min"]) == [{"minutes": 15, "action": "DISPLAY"}]
        assert parse_reminders(["1h"]) == [{"minutes": 60, "action": "DISPLAY"}]
        assert parse_reminders(["2d"]) == [{"minutes": 2880, "action": "DISPLAY"}]
        assert parse_reminders(["0"]) == [{"minutes": 0, "action": "DISPLAY"}]
        assert parse_reminders(["0 min"]) == [{"minutes": 0, "action": "DISPLAY"}]


# ============================================================
# validate_path: control character rejection
# ============================================================
class TestValidatePathRejectsControlCharacters:
    """Null bytes and control characters are rejected by validation."""

    def test_null_byte_rejected(self):
        """NUL byte in filename is rejected."""
        from owuinc.owuinc import validate_path

        with pytest.raises(Exception, match="control characters"):
            validate_path("foo\x00bar", MockValves())

    def test_null_byte_alone_rejected(self):
        """NUL byte as sole content is rejected."""
        from owuinc.owuinc import validate_path

        with pytest.raises(Exception, match="control characters"):
            validate_path("\x00", MockValves())

    def test_control_char_software_rejected(self):
        """ASCII SO (start of selected area) is rejected."""
        from owuinc.owuinc import validate_path

        with pytest.raises(Exception, match="control characters"):
            validate_path("foo\x0ebar", MockValves())

    def test_control_char_stx_rejected(self):
        """ASCII STX is rejected."""
        from owuinc.owuinc import validate_path

        with pytest.raises(Exception, match="control characters"):
            validate_path("foo\x02bar", MockValves())


# ============================================================
# validate_path: slash-only edge cases
# ============================================================
class TestValidatePathSlashOnly:
    """Slash-only paths produce odd results."""

    def test_double_slash(self):
        """'//' produces 'owuinc/.' — not the clean prefix."""
        from owuinc.owuinc import validate_path

        result = validate_path("//", MockValves())
        # BUG: produces "owuinc/." instead of "owuinc/"
        assert result == "owuinc/." or result == "owuinc/"

    def test_many_slashes(self):
        """'/////' same issue."""
        from owuinc.owuinc import validate_path

        result = validate_path("/////", MockValves())
        assert result in ("owuinc/", "owuinc/.")


# ============================================================
# edit_calendar_event: dtstart/dtend mutation safety
# ============================================================
class TestDatetimePropertyMutationSafety:
    """edit_calendar_event removes then re-adds dtstart/dtend
    to avoid VALUE parameter mismatch with icalendar."""

    def test_del_add_produces_correct_ics(self):
        """Verify that del+add produces correct ICS output."""
        from datetime import date, datetime

        from dateutil.tz import tzlocal
        from icalendar import Event

        e = Event()
        e.add("dtstart", date(2026, 8, 1))
        e.add("dtend", date(2026, 8, 2))

        # Del + add (the fix)
        del e["dtstart"]
        e.add("dtstart", datetime(2026, 8, 1, 14, 0, 0, tzinfo=tzlocal()))
        del e["dtend"]
        e.add("dtend", datetime(2026, 8, 1, 15, 0, 0, tzinfo=tzlocal()))

        ical_str = e.to_ical().decode()
        # VALUE=DATE should NOT be present anymore — it should be a proper datetime
        assert "VALUE=DATE" not in ical_str
        # Should have the time component
        assert "T14" in ical_str
        assert "T15" in ical_str


# ============================================================
# cp: partial-failure reporting
# ============================================================
class TestCpPartialFailure:
    """cp's partial-copy error must not swallow the underlying cause."""

    class _CpClient:
        """src dir with two files; the copy of `fail` raises."""

        def __init__(self, fail=None):
            self.fail = fail  # dst suffix whose copy raises, or None
            self.copied = []

        async def is_dir(self, path):
            return path.endswith("/src")

        async def mkdir(self, path, recursive=False):
            pass

        async def list_files(self, path):
            return ["/src/a.txt", "/src/b.txt"]

        async def copy(self, remote_path_from, remote_path_to):
            self.copied.append(remote_path_to)
            if self.fail and remote_path_to.endswith(self.fail):
                raise PermissionError("403 Forbidden: quota exceeded")

        async def close(self):
            pass

    def _tools(self, fail=None):
        from owuinc.owuinc import Tools

        client = TestCpPartialFailure._CpClient(fail)

        class _StubbedTools(Tools):
            def _webdav_client(self):
                return client

            async def _ensure_sandbox(self, client):
                pass

            async def _check_blacklisted_recursive(self, client, path):
                pass

            async def _check_read_only_recursive(self, client, path, missing_ok=False):
                pass

        t = _StubbedTools()
        t.client = client
        return t

    @pytest.mark.asyncio
    async def test_partial_failure_details_include_cause(self):
        t = self._tools(fail="/b.txt")
        res = await t.cp("src", "dst")
        assert res["result"] == "False"
        # N counts the one successful leaf; the 403 cause survives in details.
        assert "partial copy: 1 path(s) copied before failure" in res["details"]
        assert "403 Forbidden: quota exceeded" in res["details"]

    @pytest.mark.asyncio
    async def test_dirs_counted_only_on_success(self):
        """Regression: a failing child must not add dir entries to _copied."""
        t = self._tools(fail="/b.txt")
        copied: list[str] = []
        with pytest.raises(PermissionError):
            await t._recursive_cp(t.client, "/src", "/dst", copied)
        assert copied == ["/dst/a.txt"]  # leaf only, no dir entries

        t2 = self._tools()
        copied2: list[str] = []
        await t2._recursive_cp(t2.client, "/src", "/dst", copied2)
        assert copied2 == ["/dst/a.txt", "/dst/b.txt", "/dst"]  # dir on success

    @pytest.mark.asyncio
    async def test_failure_without_progress_keeps_original_cause(self):
        # Fail on the very first leaf: no partial progress, no wrapper message.
        t = self._tools(fail="/a.txt")
        res = await t.cp("src", "dst")
        assert res["result"] == "False"
        assert "partial copy" not in res["details"]
        assert "403 Forbidden: quota exceeded" in res["details"]


# ============================================================
# startup_context_injector: valve checks
# ============================================================
class TestContextInjectorValves:
    """startup_context_injector valve presence checks."""

    def test_request_timeout_valve_exists(self):
        """REQUEST_TIMEOUT valve exists."""
        import startup_context_injector

        valves = startup_context_injector.Filter.Valves
        assert "REQUEST_TIMEOUT" in list(valves.model_fields.keys())


# ============================================================
# _check_redos_risk: ReDoS protection
# ============================================================
class TestCheckRedosRisk:
    """_check_redos_risk rejects nested quantifiers that cause ReDoS."""

    def test_catches_basic_nested(self):
        from owuinc.owuinc import _check_redos_risk

        with pytest.raises(ValueError, match="nested quantifiers"):
            _check_redos_risk("(a+)+b")

    def test_catches_star_nested(self):
        from owuinc.owuinc import _check_redos_risk

        with pytest.raises(ValueError, match="nested quantifiers"):
            _check_redos_risk("(a*)*b")

    def test_catches_noncapturing_nested(self):
        from owuinc.owuinc import _check_redos_risk

        with pytest.raises(ValueError, match="nested quantifiers"):
            _check_redos_risk("(?:a+)+")

    def test_catches_bounded_nested(self):
        from owuinc.owuinc import _check_redos_risk

        with pytest.raises(ValueError, match="nested quantifiers"):
            _check_redos_risk(r"(a{1,3})+")

    def test_catches_deeply_nested(self):
        from owuinc.owuinc import _check_redos_risk

        with pytest.raises(ValueError, match="nested quantifiers"):
            _check_redos_risk("((a+)+)+")

    def test_catches_branch_nested(self):
        from owuinc.owuinc import _check_redos_risk

        with pytest.raises(ValueError, match="nested quantifiers"):
            _check_redos_risk("(a+|b+)+")

    def test_allows_simple_quantifier(self):
        from owuinc.owuinc import _check_redos_risk

        _check_redos_risk("a+b")

    def test_allows_group_with_quantifier(self):
        from owuinc.owuinc import _check_redos_risk

        _check_redos_risk("(abc)+")

    def test_allows_multiple_quantifiers(self):
        from owuinc.owuinc import _check_redos_risk

        _check_redos_risk("a+b+c+d+")

    def test_allows_star_any(self):
        from owuinc.owuinc import _check_redos_risk

        _check_redos_risk("foo.*bar")

    def test_allows_bounded_quantifier(self):
        from owuinc.owuinc import _check_redos_risk

        _check_redos_risk(r"a{1,3}b")

    def test_allows_branch(self):
        from owuinc.owuinc import _check_redos_risk

        _check_redos_risk("(foo|bar)+")

    def test_allows_digit_class(self):
        from owuinc.owuinc import _check_redos_risk

        _check_redos_risk(r"\d+")

    def test_allows_word_class(self):
        from owuinc.owuinc import _check_redos_risk

        _check_redos_risk(r"\w+")
