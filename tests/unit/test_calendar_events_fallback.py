"""calendar_events must not trust a suspicious time-range result set.

A buggy server may (a) return nothing, or (b) return items that clearly lie
outside the requested window — proof the time-range filter was ignored/misapplied
and the set may even be partial. Both cases fall back to the authoritative full
``cal.events()`` list, which the existing client-side window filter then trims.
A compliant server (all results in-window) is never re-fetched.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from icalendar import Event

from owuinc.owuinc import Tools

_TZ = ZoneInfo("UTC")


def _dt(y, m, d, hh=0):
    return datetime(y, m, d, hh, tzinfo=timezone.utc)


def _event(uid, summary, dtstart, dtend=None, rrule=None):
    e = Event()
    e.add("uid", uid)
    e.add("summary", summary)
    e.add("dtstart", dtstart)
    if dtend is not None:
        e.add("dtend", dtend)
    if rrule is not None:
        e.add("rrule", rrule)
    return _Ev(e)


class _Ev:
    """Shim mimicking a caldav EventResource: exposes ``.component``."""

    def __init__(self, component):
        self.component = component


class FakeCal:
    def __init__(self, search_result, all_result):
        self._search = search_result
        self._all = all_result
        self.events_called = False

    async def search(self, **kwargs):
        return self._search

    async def events(self):
        self.events_called = True
        return self._all


class FakeClient:
    async def principal(self):
        return object()

    async def close(self):
        pass


def _tools(cal):
    t = Tools()

    async def _caldav_client():
        return FakeClient()

    async def _get_calendar(principal, name):
        return cal

    t._caldav_client = _caldav_client
    t._get_calendar = _get_calendar
    return t


async def _events(cal):
    res = await _tools(cal).calendar_events(
        calendar_name="Personal", start="2026-01-01", days=1, __user__={"timezone": "UTC"}
    )
    assert res["result"] == "True"
    return res["data"]


# An in-window one-off and an out-of-window one-off.
_IN = lambda: _event("in", "In Window", _dt(2026, 1, 1, 9), _dt(2026, 1, 1, 10))
_OUT = lambda: _event("out", "Old Event", _dt(2025, 6, 1, 9), _dt(2025, 6, 1, 10))


class TestCalendarEventsFallback:
    async def test_compliant_in_window_results_are_not_refetched(self):
        cal = FakeCal(search_result=[_IN()], all_result=[])
        data = await _events(cal)
        assert cal.events_called is False
        assert any(e.get("summary") == "In Window" for e in data)

    async def test_empty_search_refetches_full_list(self):
        cal = FakeCal(search_result=[], all_result=[_IN()])
        data = await _events(cal)
        assert cal.events_called is True
        assert any(e.get("summary") == "In Window" for e in data)

    async def test_partial_out_of_window_results_trigger_refetch_and_repair(self):
        # search returns only an out-of-window event and omits the real one:
        # trusting it would silently drop the valid in-window event.
        cal = FakeCal(search_result=[_OUT()], all_result=[_IN(), _OUT()])
        data = await _events(cal)
        assert cal.events_called is True
        summaries = {e.get("summary") for e in data}
        assert "In Window" in summaries  # recovered via the full fetch
        assert "Old Event" not in summaries  # client-side window filter drops it

    async def test_recurring_master_before_window_is_not_a_false_positive(self):
        # A compliant server returns a recurring master whose DTSTART precedes
        # the window because an instance overlaps; it must NOT trigger a refetch.
        master = _event(
            "rec", "Recurring", _dt(2025, 12, 31, 9), rrule={"freq": "DAILY"}
        )
        cal = FakeCal(search_result=[master, _IN()], all_result=[])
        data = await _events(cal)
        assert cal.events_called is False
        summaries = {e.get("summary") for e in data}
        assert "In Window" in summaries
        assert "Recurring" in summaries  # expanded to its in-window occurrence

    async def test_window_boundary_outside_event_is_flagged(self):
        # An event starting after the window also proves the filter was ignored.
        after = _event(
            "late", "Far Future", _dt(2027, 1, 1, 9), _dt(2027, 1, 1, 10)
        )
        cal = FakeCal(search_result=[after, _IN()], all_result=[_IN(), after])
        data = await _events(cal)
        assert cal.events_called is True
        summaries = {e.get("summary") for e in data}
        assert "In Window" in summaries
        assert "Far Future" not in summaries
