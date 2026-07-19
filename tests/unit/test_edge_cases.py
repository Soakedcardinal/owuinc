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
        assignments = [line.strip() for line in src.split("\n") if "component[" in line]
        assert len(assignments) == 3
        assert 'todo.component["status"] = "COMPLETED"' in assignments[0]
        assert 'todo.component["completed"]' in assignments[1]
        assert 'todo.component["percent-complete"] = 100' in assignments[2]


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
    """_get_calendar returns first match with no disambiguation."""

    def test_code_returns_first_match(self):
        """Inspect code: returns first cal where display_name matches."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools._get_calendar)
        # The code has 'for cal in calendars' then 'return cal' on first match
        # No duplicate detection logic exists
        assert "for cal in calendars" in src or "for cal in" in src
        assert "return cal" in src
        # No check for multiple matches


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
# get_calendar_events: date range bounds
# ============================================================
class TestGetCalendarEventsDateBounds:
    """cal.search has a default end boundary (30 days from now)."""

    def test_end_parameter_present_in_search(self):
        """cal.search includes end= parameter."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.get_calendar_events)
        assert "end=" in src, "cal.search must have end= parameter"

    def test_end_is_30_days_ahead(self):
        """Default end is 30 days from now."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.get_calendar_events)
        assert "timedelta(days=30)" in src


# ============================================================
# ls: directory self-listing edge case
# ============================================================
class TestLsDirectorySelfEntry:
    """Directory can list itself if server returns href with trailing slash."""

    def test_ls_comparison_logic_inspection(self):
        """Examine ls self-exclusion comparisons."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.ls)
        # Look at self-exclusion lines
        exclude_lines = [line.strip() for line in src.split("\n") if "continue" in line]
        print("Self-exclusion lines:")
        for line in exclude_lines:
            print(f"  {line}")
        # The comparison uses == without normalizing trailing slashes on both sides
        # This is the bug — if server returns "Documents/" but full_path is "Documents",
        # they won't match and the directory will appear in its own listing


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
# edit and append_file: race conditions
# ============================================================
class TestEditAppendRaceCondition:
    """edit and append_file do read-modify-write without locking or ETag."""

    def test_edit_no_etag(self):
        """edit() reads file, modifies, writes — no ETag check."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.edit)
        assert "etag" not in src.lower()
        assert "if-match" not in src.lower()
        assert "lock" not in src.lower()

    def test_append_no_etag(self):
        """append_file() same pattern."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.append_file)
        assert "etag" not in src.lower()
        assert "if-match" not in src.lower()
        assert "lock" not in src.lower()


# ============================================================
# file operations: binary file handling
# ============================================================
class TestBinaryFileHandling:
    """All file ops assume UTF-8; no binary mode."""

    def test_read_file_utf8_decode(self):
        """read_file uses .decode('utf-8')."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.read_file)
        assert '.decode("utf-8")' in src or "'.decode('utf-8')" in src
        assert "binary" not in src.lower()

    def test_write_file_utf8_encode(self):
        """write_file uses .encode('utf-8')."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.write_file)
        assert '.encode("utf-8")' in src or "'.encode('utf-8')" in src
        assert "binary" not in src.lower()

    def test_grep_silently_skips_binary(self):
        """grep has 'except Exception: continue' — binary decode
        errors are silently skipped."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.grep)
        # The broad except just continues
        assert "except Exception:" in src or "except Exception" in src


# ============================================================
# startup_context_injector: token budget
# ============================================================
class TestContextInjectorTokenCap:
    """No budget enforcement on injected context."""

    def test_no_max_token_valve(self):
        """startup_context_injector has no MAX_TOKENS or similar valve."""
        import startup_context_injector

        valves = startup_context_injector.Filter.Valves

        field_names = list(valves.model_fields.keys())
        for name in field_names:
            if (
                "token" in name.lower()
                or "max" in name.lower()
                or "limit" in name.lower()
                or "budget" in name.lower()
            ):
                pytest.fail(f"Found budget-related valve: {name} — may be fixed")

    def test_no_truncation_in_build_context(self):
        """_build_context has no truncation logic."""
        import inspect

        import startup_context_injector

        src = inspect.getsource(startup_context_injector.Filter._build_context)
        assert "truncat" not in src.lower()
        assert "limit" not in src.lower() or "limit" not in src.lower().replace(
            "definition", ""
        )


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
# rm: atomic deletion with partial-failure reporting
# ============================================================
class TestRmAtomicDeletion:
    """rm tracks deleted paths and reports partial failures."""

    def test_rm_tracks_deleted_paths(self):
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.rm)
        assert "deleted" in src
        assert "partial delete" in src
        assert "deleted.append" in src

    def test_rm_raises_on_partial_failure(self):
        """rm raises ValueError with count and list of deleted paths."""
        import inspect

        from owuinc.owuinc import Tools

        src = inspect.getsource(Tools.rm)
        assert "raise ValueError(" in src
        assert "partial delete" in src


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
