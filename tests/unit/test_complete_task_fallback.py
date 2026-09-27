"""Recurring-completion fallback must fire ONLY for known-unsupported
recurrence. An unrelated failure (network, auth, 5xx, unexpected bug) must
propagate instead of silently marking an entire recurring series completed.
"""

from owuinc.owuinc import Tools


class FakeComp:
    """Minimal, case-insensitive icalendar-component stand-in (real icalendar
    components are case-insensitive, which is why the tool's ``pop("rrule")``
    removes an ``RRULE`` stored in any case)."""

    def __init__(self, **props):
        self._d = {k.lower(): v for k, v in props.items()}

    def get(self, key, default=None):
        return self._d.get(key.lower(), default)

    def __contains__(self, key):
        return key.lower() in self._d

    def pop(self, key, default=None):
        return self._d.pop(key.lower(), default)

    def add(self, key, value):
        self._d[key.lower()] = value


class FakeTodo:
    def __init__(self, complete_exc=None):
        self.component = FakeComp(SUMMARY="Standup", RRULE="FREQ=DAILY;COUNT=5")
        self._complete_exc = complete_exc
        self.complete_kwargs = None
        self.save_called = False

    async def complete(self, **kwargs):
        self.complete_kwargs = kwargs
        if self._complete_exc is not None:
            raise self._complete_exc

    async def save(self):
        self.save_called = True


class FakeClient:
    async def principal(self):
        return object()

    async def close(self):
        pass


def _tools(todo):
    t = Tools()
    t.valves.TASK_LIST_WHITELIST = "Tasks"

    async def _caldav_client():
        return FakeClient()

    async def _get_calendar(principal, name):
        return object()

    async def _find_task_by_summary(cal, summary, due=None, description_contains=None):
        return todo

    t._caldav_client = _caldav_client
    t._get_calendar = _get_calendar
    t._find_task_by_summary = _find_task_by_summary
    return t


class TestRecurringCompletionFallback:
    async def test_valid_recurring_continues_series(self):
        todo = FakeTodo()
        result = await _tools(todo).complete_task("Standup", list_name="Tasks")
        assert result["result"] == "True"
        assert "series continues" in result["data"]
        assert todo.complete_kwargs["handle_rrule"] is True
        assert todo.complete_kwargs["rrule_mode"] == "safe"
        # the destructive plain-completion save must not run on this path
        assert todo.save_called is False

    async def test_network_error_propagates_without_ending_series(self):
        todo = FakeTodo(complete_exc=ConnectionError("socket reset"))
        result = await _tools(todo).complete_task("Standup", list_name="Tasks")
        # caldav_safe turns the propagated error into a failed result...
        assert result["result"] == "False"
        # ...and crucially NO destructive plain completion happened.
        assert todo.save_called is False

    async def test_unexpected_bug_propagates(self):
        todo = FakeTodo(complete_exc=RuntimeError("bug in library"))
        result = await _tools(todo).complete_task("Standup", list_name="Tasks")
        assert result["result"] == "False"
        assert todo.save_called is False

    async def test_malformed_recurrence_still_falls_back(self):
        # caldav signals an unmodellable recurrence with ValueError; the
        # documented plain-completion fallback still runs and completes the
        # master (drops RRULE, marks COMPLETED) rather than propagating.
        todo = FakeTodo(complete_exc=ValueError("malformed RRULE"))
        result = await _tools(todo).complete_task("Standup", list_name="Tasks")
        assert result["result"] == "True"
        assert todo.save_called is True
        assert "RRULE" not in todo.component
        assert todo.component.get("status") == "COMPLETED"

    async def test_notimplemented_recurrence_still_falls_back(self):
        todo = FakeTodo(complete_exc=NotImplementedError("exotic recurrence"))
        result = await _tools(todo).complete_task("Standup", list_name="Tasks")
        assert result["result"] == "True"
        assert todo.save_called is True
        assert "RRULE" not in todo.component
        assert todo.component.get("status") == "COMPLETED"

    async def test_entire_series_ends_series(self):
        todo = FakeTodo()
        result = await _tools(todo).complete_task(
            "Standup", list_name="Tasks", entire_series=True
        )
        assert result["result"] == "True"
        assert "series ended" in result["data"]
        assert todo.complete_kwargs is None  # per-occurrence path skipped
        assert todo.save_called is True
        assert "RRULE" not in todo.component  # a completed series must not recur
        assert todo.component.get("status") == "COMPLETED"
