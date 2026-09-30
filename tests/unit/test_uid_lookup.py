"""Unit tests for UID-based lookup helpers and the CRUD uid/summary guards.

The helpers are pure given a fake collection, so no CalDAV server is needed.
"""

import pytest
from caldav.lib.error import NotFoundError

from owuinc.owuinc import Tools


class FakeComp:
    """Case-insensitive icalendar-component stand-in. Underscores in the
    constructor map to hyphens so RECURRENCE_ID lands under 'recurrence-id',
    matching real icalendar property names."""

    @staticmethod
    def _norm(key: str) -> str:
        return key.lower().replace("_", "-")

    def __init__(self, **props):
        self._d = {self._norm(k): v for k, v in props.items()}

    def get(self, key, default=None):
        return self._d.get(self._norm(key), default)

    def __getitem__(self, key):
        return self._d[self._norm(key)]


class FakeObj:
    def __init__(self, **props):
        self.component = FakeComp(**props)


class FakeCal:
    def __init__(self, todos=None, events=None):
        self._todos = todos or []
        self._events = events or []

    async def todos(self, *a, **k):
        return self._todos

    async def events(self, *a, **k):
        return self._events


class FakeClient:
    async def principal(self):
        return object()

    async def close(self):
        pass


def _guard_tools():
    """Tools wired so a CRUD call reaches the uid/summary guard without a server."""
    t = Tools()

    async def _caldav_client():
        return FakeClient()

    async def _get_calendar(principal, name):
        return object()

    t._caldav_client = _caldav_client
    t._get_calendar = _get_calendar
    return t


class TestFindTaskByUid:
    async def test_finds_by_uid(self):
        cal = FakeCal(
            todos=[FakeObj(UID="u1", SUMMARY="A"), FakeObj(UID="u2", SUMMARY="B")]
        )
        found = await Tools()._find_task_by_uid(cal, "u2")
        assert found.component["summary"] == "B"

    async def test_prefers_open_over_completed(self):
        cal = FakeCal(
            todos=[
                FakeObj(UID="uX", SUMMARY="done", STATUS="COMPLETED"),
                FakeObj(UID="uX", SUMMARY="live"),
            ]
        )
        found = await Tools()._find_task_by_uid(cal, "uX")
        assert found.component["summary"] == "live"

    async def test_raises_when_absent(self):
        cal = FakeCal(todos=[FakeObj(UID="u1", SUMMARY="A")])
        with pytest.raises(NotFoundError):
            await Tools()._find_task_by_uid(cal, "missing")


class TestFindEventByUid:
    async def test_returns_master_not_override(self):
        cal = FakeCal(
            events=[
                FakeObj(UID="e1", SUMMARY="Standup"),
                FakeObj(UID="e1", SUMMARY="Moved", RECURRENCE_ID="20260101T090000Z"),
            ]
        )
        found = await Tools()._find_event_by_uid(cal, "e1")
        assert found.component["summary"] == "Standup"

    async def test_override_only_is_not_found(self):
        cal = FakeCal(
            events=[
                FakeObj(UID="e2", SUMMARY="Moved", RECURRENCE_ID="20260101T090000Z")
            ]
        )
        with pytest.raises(NotFoundError):
            await Tools()._find_event_by_uid(cal, "e2")


class TestResolveTaskUid:
    async def test_uid_wins_over_summary(self):
        cal = FakeCal(todos=[FakeObj(UID="abc", SUMMARY="Foo")])
        assert await Tools()._resolve_task_uid(cal, "abc") == "abc"

    async def test_summary_still_resolves(self):
        cal = FakeCal(todos=[FakeObj(UID="abc", SUMMARY="Foo")])
        assert await Tools()._resolve_task_uid(cal, "  foo ") == "abc"

    async def test_raises_when_absent(self):
        cal = FakeCal(todos=[FakeObj(UID="abc", SUMMARY="Foo")])
        with pytest.raises(Exception):
            await Tools()._resolve_task_uid(cal, "nope")

    async def test_absent_identifier_error_names_uid_or_summary(self):
        """The caller may pass either a uid or a summary; the error must not
        claim 'summary' when a missing uid was given."""
        cal = FakeCal(todos=[FakeObj(UID="abc", SUMMARY="Foo")])
        with pytest.raises(Exception, match="uid or summary.*not found"):
            await Tools()._resolve_task_uid(cal, "nope")

    async def test_summaryless_todos_do_not_match(self):
        """A malformed VTODO without SUMMARY must not crash the lookup on a
        bare 'summary' KeyError, nor match a summary search: only the real
        summary carrier resolves."""
        cal = FakeCal(todos=[FakeObj(UID="abc"), FakeObj(UID="u2", SUMMARY="Foo")])
        assert await Tools()._resolve_task_uid(cal, "  foo ") == "u2"
        # The exact UID of the summaryless todo still wins over any summary.
        assert await Tools()._resolve_task_uid(cal, "abc") == "abc"

    async def test_summaryless_todos_never_match_summaries(self):
        cal = FakeCal(todos=[FakeObj(UID="u1"), FakeObj(UID="u2", SUMMARY="Foo")])
        with pytest.raises(Exception, match="not found"):
            await Tools()._resolve_task_uid(cal, "  nope ")


class TestCrudRequiresTarget:
    """Each CRUD tool refuses when neither uid nor summary is given."""

    @pytest.mark.parametrize(
        "method",
        [
            "edit_task",
            "complete_task",
            "delete_task",
            "edit_calendar_event",
            "delete_calendar_event",
        ],
    )
    async def test_neither_uid_nor_summary(self, method):
        res = await getattr(_guard_tools(), method)()
        assert res["result"] == "False"
        assert "provide uid or summary" in res["details"]
