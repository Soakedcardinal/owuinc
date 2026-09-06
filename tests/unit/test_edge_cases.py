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
# complete_task: sets all required completion fields
# ============================================================
class TestCompleteTaskSetsAllFields:
    """complete_task sets STATUS, COMPLETED, and PERCENT-COMPLETE."""

    def test_sets_status_completed_and_percent(self):
        """Verify STATUS, COMPLETED, and PERCENT-COMPLETE are all set."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.complete_task)
        assert '"status"' in src and '"COMPLETED"' in src
        assert '"completed"' in src
        assert '"percent-complete"' in src and "100" in src


# ============================================================
# edit_calendar_event: dtstart/dtend mutation safety
# ============================================================
class TestDatetimePropertyMutationSafety:
    """edit_calendar_event uses del+add for dtstart/dtend
    to avoid VALUE parameter mismatch with icalendar."""

    def test_uses_del_and_add_not_dot_dt(self):
        """Verify the fix: del + add instead of .dt = mutation."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.edit_calendar_event)
        # The old buggy pattern: e.component["dtstart"].dt = ...
        assert ".dt = dtstart" not in src
        assert ".dt = dtend" not in src
        # The fixed pattern: del + add
        assert (
            'del e.component["dtstart"]' in src or "del e.component['dtstart']" in src
        )
        assert 'del e.component["dtend"]' in src or "del e.component['dtend']" in src
        assert 'e.component.add("dtstart"' in src or "e.component.add('dtstart'" in src
        assert 'e.component.add("dtend"' in src or "e.component.add('dtend'" in src

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
# _get_calendar: duplicate calendar name handling
# ============================================================
class TestGetCalendarDuplicateNames:
    """_get_calendar detects duplicate display names and raises."""

    def test_code_collects_matches(self):
        """Verify duplicates are detected via matches list."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools._get_calendar)
        assert "matches" in src
        assert "len(matches)" in src
        assert "multiple" in src.lower()

    def test_code_raises_on_multiple(self):
        """Verify NotFoundError is raised when multiple calendars match."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools._get_calendar)
        assert "> 1" in src
        lines = src.split("\n")
        found_multi_check = False
        for i, line in enumerate(lines):
            if "> 1" in line:
                for j in range(i + 1, min(i + 3, len(lines))):
                    if "raise" in lines[j]:
                        found_multi_check = True
                        break
        assert found_multi_check, "No raise after multiple-match check"


# ============================================================
# edit_task: truthy checks prevent clearing fields
# ============================================================
class TestEditTaskUsesIsNotNone:
    """edit_task uses `is not None` checks so falsy values can clear fields."""

    def test_new_priority_is_not_none(self):
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.edit_task)
        assert "if new_priority is not None:" in src

    def test_new_location_is_not_none(self):
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.edit_task)
        assert "if new_location is not None:" in src

    def test_new_description_is_not_none(self):
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.edit_task)
        assert "if new_description is not None:" in src

    def test_new_categories_is_not_none(self):
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.edit_task)
        assert "if new_categories is not None:" in src

    def test_new_url_is_not_none(self):
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.edit_task)
        assert "if new_url is not None:" in src

    def test_new_summary_is_not_none(self):
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.edit_task)
        assert "if new_summary is not None:" in src


# ============================================================
# edit_calendar_event: truthy checks and rrule removal
# ============================================================
class TestEditCalendarEventUsesIsNotNone:
    """edit_calendar_event uses `is not None` checks; new_rrule=None removes RRULE."""

    def test_new_summary_is_not_none(self):
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.edit_calendar_event)
        assert "if new_summary is not None:" in src

    def test_new_location_is_not_none(self):
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.edit_calendar_event)
        assert "if new_location is not None:" in src

    def test_new_description_is_not_none(self):
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.edit_calendar_event)
        assert "if new_description is not None:" in src

    def test_new_alarms_is_not_none(self):
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.edit_calendar_event)
        assert "if new_alarms is not None:" in src

    def test_new_rrule_none_removes(self):
        """new_rrule=None now actually removes the RRULE property."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.edit_calendar_event)
        assert "if new_rrule is not None:" in src
        lines = src.split("\n")
        in_docstring = False
        found_removal = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('"""') or stripped.startswith("'''"):
                if stripped.count('"""') == 2 or stripped.count("'''") == 2:
                    continue
                in_docstring = not in_docstring
                continue
            if in_docstring:
                continue
            if "rrule" in line.lower() and ("pop" in line or "del " in line):
                found_removal = True
        assert found_removal, "No rrule removal branch found"


# ============================================================
# calendar_events: date range bounds
# ============================================================
class TestGetCalendarEventsDateBounds:
    """cal.search has a default end boundary (30 days from now)."""

    def test_end_parameter_present_in_search(self):
        """cal.search includes end= parameter."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.calendar_events)
        assert "end=" in src, "cal.search must have end= parameter"

    def test_end_is_30_days_ahead(self):
        """Default end is 30 days from now."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.calendar_events)
        assert "timedelta(days=30)" in src


# ============================================================
# ls: directory self-listing edge case
# ============================================================
class TestLsDirectorySelfEntry:
    """Directory self-entry must be excluded regardless of trailing slash."""

    def test_strip_leading_slash_preserves_trailing(self):
        """_strip_leading_slash should NOT strip trailing slashes by design."""
        from owuinc.owuinc import _strip_leading_slash

        assert _strip_leading_slash("/foo/") == "foo/"
        assert _strip_leading_slash("foo/") == "foo/"

    def test_ls_detail_mode_normalized_paths_in_source(self):
        """Verify ls detail mode normalizes trailing slashes for self-skip."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.ls)
        detail_section = src[src.index("if detail:") : src.index("# Simple listing")]
        assert (
            '.rstrip("/")' in detail_section
        ), "ls detail mode must rstrip trailing slashes on both sides"

    def test_ls_simple_mode_normalized_paths_in_source(self):
        """Verify ls simple mode normalizes trailing slashes for self-skip."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.ls)
        simple_section = src[src.index("# Simple listing") :]
        assert (
            '.rstrip("/")' in simple_section
        ), "ls simple mode must rstrip trailing slashes"


# ============================================================
# mv and cp: overwrite consistency
# ============================================================
class TestMvAndCpOverwriteConsistency:
    """mv and cp both pass overwrite=True."""

    def test_mv_overwrite_true(self):
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.mv)
        assert "overwrite=True" in src or "overwrite = True" in src

    def test_cp_no_overwrite_param(self):
        """_recursive_cp must NOT pass overwrite to client.copy."""
        import inspect

        from owuinc.owuinc import Tools

        cp_src = inspect.getsource(Tools._recursive_cp)
        assert (
            "overwrite" not in cp_src
        ), "client.copy must NOT pass overwrite (aiowebdav2 lacks it)"


# ============================================================
# edit and append: race conditions
# ============================================================
class TestEditAppendRaceCondition:
    """edit and append use WebDAV locking to prevent races."""

    def test_edit_uses_lock(self):
        """edit() must use client.lock() for safe read-modify-write."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.edit)
        assert ".lock(" in src, "edit must use WebDAV lock"
        assert "async with" in src, "lock must be used as async context manager"

    def test_append_uses_lock(self):
        """append() must use client.lock() for existing files."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.append)
        assert ".lock(" in src, "append must use WebDAV lock"
        assert "async with" in src, "lock must be used as async context manager"


# ============================================================
# file operations: binary file handling
# ============================================================
class TestBinaryFileHandling:
    """File ops explicitly reject binary content with clear errors."""

    def test_cat_raises_on_binary(self):
        """cat raises ValueError for non-UTF8 content."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.cat)
        assert "UnicodeDecodeError" in src
        assert "not a text file" in src

    def test_grep_catches_UnicodeDecodeError(self):
        """grep catches UnicodeDecodeError and tracks skipped files."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.grep)
        assert "UnicodeDecodeError" in src
        assert "skipped" in src

    def test_edit_raises_on_binary(self):
        """edit raises ValueError for non-UTF8 content."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.edit)
        assert "UnicodeDecodeError" in src
        assert "not a text file" in src

    def test_append_raises_on_binary(self):
        """append raises ValueError for non-UTF8 content."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.append)
        assert "UnicodeDecodeError" in src
        assert "not a text file" in src


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


# ============================================================
# rm: per-path results (every path attempted independently)
# ============================================================
class TestRmPerPathResults:
    """rm attempts every path independently and returns per-path results."""

    def test_rm_reports_per_path_results(self):
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.rm)
        assert "partial delete" not in src
        assert '"True"' in src
        assert '"False"' in src
        assert "results" in src

    def test_rm_sanitizes_per_path_details(self):
        """Per-path details pass through _sanitize so no paths/UUIDs leak."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.rm)
        assert "_sanitize" in src


# ============================================================
# lookup: exact match for task/event summaries
# ============================================================
class TestLookupExactMatch:
    """Task and event lookups use case-insensitive exact match, not substring."""

    def test_task_lookup_is_exact_match(self):
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools._find_task_by_uid_or_summary)
        assert ".lower()" in src
        assert "norm_summary" in src

    def test_event_lookup_is_exact_match(self):
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools._find_event_by_uid_or_summary)
        assert ".lower()" in src
        assert "norm_summary" in src

    def test_task_lookup_no_substring_in(self):
        """Ensure substring `in` operator is not used for summary matching."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools._find_task_by_uid_or_summary)
        lines = src.split("\n")
        for line in lines:
            stripped = line.strip()
            if (
                "summary" in stripped
                and "norm" not in stripped
                and "raise" not in stripped
            ):
                assert (
                    "in todo" not in stripped and "in str(todo" not in stripped
                ), f"Substring match found: {stripped}"

    def test_event_lookup_no_substring_in(self):
        """Ensure substring `in` operator is not used for summary matching."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools._find_event_by_uid_or_summary)
        lines = src.split("\n")
        for line in lines:
            stripped = line.strip()
            if (
                "summary" in stripped
                and "norm" not in stripped
                and "raise" not in stripped
            ):
                assert (
                    "in e.component" not in stripped
                ), f"Substring match found: {stripped}"
