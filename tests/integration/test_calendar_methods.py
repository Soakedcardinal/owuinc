"""Integration tests for CalDAV methods using local Radicale server.

All tests are async to match the async tool methods in owuinc.py.
Note: caldav.aio only has principal() as async; other methods are sync
wrappers that delegate to the async client internally.
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio

from owuinc.owuinc import is_whitelisted

logger = logging.getLogger(__name__)


async def _get_calendar(caldav_tools, calendar_name: str):
    """Get a calendar by name, using the Tools._get_calendar helper."""
    client = await caldav_tools._caldav_client()
    principal = await client.principal()
    return await caldav_tools._get_calendar(principal, calendar_name)


async def _make_calendar(principal, name: str, cal_id: str):
    """Idempotently create a calendar, deleting any leftover first."""
    for cal in await principal.get_calendars():
        try:
            dn = await cal.get_display_name()
        except Exception:
            dn = None
        if dn == name or cal.url.path.rstrip("/").endswith("/" + cal_id):
            await cal.delete()
    return await principal.make_calendar(name=name, cal_id=cal_id)


class TestServerHealth:
    """Tests for Radicale server health and connectivity."""

    def test_radicale_server_starts_and_responds(self, radicale_server):
        """Verify Radicale server starts successfully and is accessible."""
        import socket

        url = radicale_server["url"]
        host, port = url.replace("http://", "").split(":")

        logger.info(f">>> [TEST] Verifying TCP connectivity to {host}:{port}")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex((host, int(port)))
        sock.close()

        logger.info(f">>> [TEST] socket.connect_ex returned {result} (0 = open)")
        assert result == 0, f"Radicale server not accessible at {url}"

        # Also do an HTTP request to verify the server is actually serving
        import urllib.request

        logger.info(f">>> [TEST] Sending HTTP OPTIONS to {url}")
        req = urllib.request.Request(url, method="OPTIONS")
        with urllib.request.urlopen(req, timeout=3) as resp:
            status = resp.status
            headers = dict(resp.headers)
            logger.info(f">>> [TEST] HTTP OPTIONS -> {status}")
            logger.info(
                ">>> [TEST] Response headers: %s",
                dict((k, headers[k]) for k in sorted(headers)),
            )
        assert status in (200, 207), f"Unexpected status: {status}"

    def test_basic_auth_works(self, radicale_server):
        """Verify htpasswd basic auth round-trip: 401 without creds, 207 with creds."""
        import urllib.request

        url = radicale_server["url"]

        # Without credentials should get 401
        logger.info(">>> [TEST] PROPFIND without credentials (expect 401)")
        req = urllib.request.Request(url, method="PROPFIND")
        req.add_header("Depth", "1")
        try:
            urllib.request.urlopen(req, timeout=3)
            assert False, "Expected 401 without credentials"
        except urllib.error.HTTPError as exc:
            logger.info(f">>> [TEST] No-auth PROPFIND -> {exc.code}")
            assert exc.code == 401, f"Expected 401, got {exc.code}"

        # With credentials should get 207
        logger.info(">>> [TEST] PROPFIND with credentials (expect 207)")
        req = urllib.request.Request(url, method="PROPFIND")
        req.add_header("Depth", "1")
        credentials = f"{radicale_server['username']}:{radicale_server['password']}"
        import base64

        req.add_header(
            "Authorization", f"Basic {base64.b64encode(credentials.encode()).decode()}"
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            status = resp.status
            logger.info(f">>> [TEST] Auth PROPFIND -> {status}")
        assert status == 207, f"Expected 207, got {status}"

    def test_wrong_password_rejected(self, radicale_server):
        """Verify that wrong credentials are rejected."""
        import base64
        import urllib.request

        url = radicale_server["url"]
        credentials = f"{radicale_server['username']}:wrongpassword"
        req = urllib.request.Request(url, method="PROPFIND")
        req.add_header("Depth", "1")
        req.add_header(
            "Authorization", f"Basic {base64.b64encode(credentials.encode()).decode()}"
        )
        try:
            urllib.request.urlopen(req, timeout=3)
            assert False, "Expected 401 with wrong password"
        except urllib.error.HTTPError as exc:
            logger.info(f">>> [TEST] Wrong password PROPFIND -> {exc.code}")
            assert exc.code == 401, f"Expected 401, got {exc.code}"


class TestCaldavClient:
    """Tests for the caldav_client fixture override and client connectivity."""

    @pytest.mark.asyncio
    async def test_caldav_client_is_correct_type(self, caldav_tools):
        """Verify the overridden caldav_client returns an AsyncDAVClient instance."""
        client = await caldav_tools._caldav_client()
        from caldav.aio import AsyncDAVClient

        logger.info(f">>> [TEST] caldav_client type: {type(client).__name__}")
        assert isinstance(
            client, AsyncDAVClient
        ), f"Expected AsyncDAVClient, got {type(client).__name__}"

    @pytest.mark.asyncio
    async def test_caldav_client_url_is_radicale_root(self, caldav_tools):
        """Verify the client points at Radicale root, NOT /remote.php/dav."""
        client = await caldav_tools._caldav_client()
        url_str = str(client.url)
        logger.info(f">>> [TEST] caldav_client.url: {url_str}")
        assert (
            url_str == "http://127.0.0.1:5232"
        ), f"Expected Radicale root URL, got {url_str}"
        assert (
            "remote.php" not in url_str
        ), f"Client URL should not contain remote.php/dav: {url_str}"

    @pytest.mark.asyncio
    async def test_principal_discovery_works(self, caldav_tools):
        """Verify the caldav library can discover the principal against Radicale."""
        client = await caldav_tools._caldav_client()
        principal = await client.principal()
        logger.info(
            f">>> [TEST] Principal: {principal}, type: {type(principal).__name__}"
        )
        assert principal is not None, "Principal discovery failed"

    @pytest.mark.asyncio
    async def test_caldav_client_property_restored_after_test(self, caldav_tools):
        """Verify the override is active during test.

        Restoration happens in the fixture's finally block.
        """
        client = await caldav_tools._caldav_client()
        url_str = str(client.url)
        logger.info(f">>> [TEST] During test, client.url: {url_str}")
        assert (
            "5232" in url_str
        ), f"Override should be active during test, got {url_str}"
        logger.info(
            ">>> [TEST] Override is active; restoration verified in conftest teardown log"
        )


class TestCalendars:
    """Tests for calendars() tool method."""

    @pytest.mark.asyncio
    async def test_calendars_returns_empty_when_no_calendars(self, caldav_tools):
        """Verify calendars returns empty list when no calendars exist.

        Fresh Radicale server starts with no calendars, so this tests that case.
        This is a REAL integration test against the actual Radicale server.
        """
        logger.info(">>> [TEST] === calendars test START ===")
        logger.info(
            ">>> [TEST] Tools instance: base_url=%s, username=%s, sandbox=%s",
            caldav_tools.valves.NEXTCLOUD_BASE_URL,
            caldav_tools.valves.NEXTCLOUD_USERNAME,
            caldav_tools.valves.SANDBOX_DIR,
        )

        # Verify the caldav_client property is wired correctly
        client = await caldav_tools._caldav_client()
        logger.info(f">>> [TEST] caldav_client type: {type(client).__name__}")
        logger.info(f">>> [TEST] caldav_client.url: {client.url}")

        # Call the method under test
        logger.info(">>> [TEST] Calling caldav_tools.calendars() ...")
        result = await caldav_tools.calendars()
        logger.info(f">>> [TEST] Raw result: {result!r}")

        # Assert structure
        logger.info(f">>> [TEST] result['result'] = {result.get('result')!r}")
        assert (
            result["result"] == "True"
        ), f"Expected result='True', got {result.get('result')!r}. Details: {result.get('details', 'N/A')!r}"

        logger.info(f">>> [TEST] 'data' key present: {'data' in result}")
        assert "data" in result, "Result missing 'data' key"

        logger.info(f">>> [TEST] result['data'] = {result['data']!r}")
        logger.info(
            f">>> [TEST] type(result['data']) = {type(result['data']).__name__}"
        )
        # Empty calendar list is expected on fresh server
        assert (
            result["data"] == []
        ), f"Expected empty calendar list, got {result['data']!r}"

        logger.info(">>> [TEST] === calendars test PASS ===")

    @pytest.mark.asyncio
    async def test_calendars_result_has_correct_structure(self, caldav_tools):
        """Verify the caldav_safe wrapper produces the expected dict structure."""
        result = await caldav_tools.calendars()

        # Must be a dict
        assert isinstance(result, dict), f"Expected dict, got {type(result).__name__}"

        # Must have 'result' key with string value
        assert "result" in result, "Missing 'result' key"
        assert isinstance(
            result["result"], str
        ), f"'result' must be str, got {type(result['result'])}"
        assert result["result"] in (
            "True",
            "False",
        ), f"'result' must be 'True' or 'False', got {result['result']!r}"

        # When result is True, must have 'data' key with list value
        if result["result"] == "True":
            assert "data" in result, "Missing 'data' key when result is True"
            assert isinstance(
                result["data"], list
            ), f"'data' must be list, got {type(result['data'])}"


class TestTaskLists:
    """Tests for task_lists() tool method."""

    @pytest.mark.asyncio
    async def test_task_lists_returns_only_whitelisted(self, caldav_tools):
        """Verify task_lists returns only whitelisted calendars.

        'Tasks' is a Nextcloud default and may exist from prior runs, so we
        only assert structural correctness and that the whitelist filters
        correctly.
        """
        result = await caldav_tools.task_lists()
        assert result["result"] == "True"
        assert "data" in result
        assert isinstance(result["data"], list)
        for item in result["data"]:
            assert is_whitelisted(caldav_tools.valves.TASK_LIST_WHITELIST, item)


class TestTaskOperations:
    """Tests for task CRUD operations.

    Covers: add_task, tasks, edit_task, complete_task, delete_task.
    """

    @pytest_asyncio.fixture
    async def tasks_calendar(self, caldav_tools):
        """Create a Tasks calendar on the Radicale server."""
        client = await caldav_tools._caldav_client()
        principal = await client.principal()
        cal = await _make_calendar(principal, name="Tasks", cal_id="tasks")
        yield cal
        try:
            await cal.delete()
        except Exception:
            pass

    @pytest_asyncio.fixture
    async def personal_calendar(self, caldav_tools):
        """Create a Personal calendar on the Radicale server."""
        client = await caldav_tools._caldav_client()
        principal = await client.principal()
        cal = await _make_calendar(principal, name="Personal", cal_id="personal")
        yield cal
        try:
            await cal.delete()
        except Exception:
            pass

    @pytest.mark.asyncio
    async def test_task_lists_with_tasks_calendar(self, caldav_tools, tasks_calendar):
        """Verify task_lists returns the Tasks calendar when it exists."""
        result = await caldav_tools.task_lists()
        assert result["result"] == "True"
        assert "data" in result
        assert "Tasks" in result["data"]

    @pytest.mark.asyncio
    async def test_add_task(self, caldav_tools, tasks_calendar):
        """Verify add_task creates a task and returns its summary."""
        result = await caldav_tools.add_task(summary="Test task", list_name="Tasks")
        assert result["result"] == "True"
        assert "data" in result
        assert result["data"] == "Test task"

    @pytest.mark.asyncio
    async def test_tasks(self, caldav_tools, tasks_calendar):
        """Verify tasks returns tasks from the Tasks calendar."""
        await caldav_tools.add_task(summary="Get tasks test", list_name="Tasks")
        result = await caldav_tools.tasks(list_name="Tasks")
        assert result["result"] == "True"
        assert "data" in result
        assert isinstance(result["data"], list)
        assert len(result["data"]) >= 1
        summaries = [t.get("summary") for t in result["data"]]
        assert "Get tasks test" in summaries

    @pytest.mark.asyncio
    async def test_edit_task(self, caldav_tools, tasks_calendar):
        """Verify edit_task modifies a task's summary."""
        add_result = await caldav_tools.add_task(
            summary="Original summary", list_name="Tasks"
        )
        summary = add_result["data"]
        edit_result = await caldav_tools.edit_task(
            summary=summary, new_summary="Edited summary", list_name="Tasks"
        )
        assert edit_result["result"] == "True"
        tasks = await caldav_tools.tasks(list_name="Tasks")
        summaries = [t.get("summary") for t in tasks["data"]]
        assert "Edited summary" in summaries

    @pytest.mark.asyncio
    async def test_add_subtask(self, caldav_tools, tasks_calendar):
        """Verify add_task with parent and edit_task nest subtasks correctly."""
        parent_result = await caldav_tools.add_task(
            summary="Parent Task", list_name="Tasks"
        )
        assert parent_result["result"] == "True"

        # Child via add_task parent parameter
        child_add_result = await caldav_tools.add_task(
            summary="Child Task by add", parent="Parent Task", list_name="Tasks"
        )
        assert (
            child_add_result["result"] == "True"
        ), f"add child by parent: {child_add_result}"

        # Child via edit_task new_related_to
        child_result = await caldav_tools.add_task(
            summary="Child Task by edit", list_name="Tasks"
        )
        assert child_result["result"] == "True"
        child_summary = child_result["data"]

        edit_result = await caldav_tools.edit_task(
            summary=child_summary, new_related_to="Parent Task", list_name="Tasks"
        )
        assert edit_result["result"] == "True", f"edit_task for nesting: {edit_result}"

        tasks = await caldav_tools.tasks(list_name="Tasks")
        assert tasks["result"] == "True", f"tasks failed: {tasks}"
        assert len(tasks["data"]) >= 1, f"tasks missing: {tasks}"

        parent_node = None
        all_summaries = []

        def collect_summaries(nodes):
            for node in nodes:
                s = node.get("summary")
                all_summaries.append(s)
                if s == "Parent Task":
                    nonlocal parent_node
                    parent_node = node
                if "subtasks" in node:
                    collect_summaries(node["subtasks"])

        collect_summaries(tasks["data"])

        assert (
            parent_node is not None
        ), f"Parent Task should exist in results. All summaries: {all_summaries}, full data: {tasks['data']}"
        assert "subtasks" in parent_node, "Parent Task should have subtasks"
        child_summaries = [s.get("summary") for s in parent_node["subtasks"]]
        assert (
            "Child Task by add" in child_summaries
        ), f"Child Task by add should be nested under Parent Task, got {child_summaries}"
        assert (
            "Child Task by edit" in child_summaries
        ), f"Child Task by edit should be nested under Parent Task, got {child_summaries}"

    @pytest.mark.asyncio
    async def test_complete_task(self, caldav_tools, tasks_calendar):
        """Verify complete_task marks a task as COMPLETED.

        Note: tasks filters out completed tasks (expected CalDAV behavior),
        so we can't verify via round-trip through tasks. We verify the
        method returns success and the task is no longer in tasks results.
        """
        add_result = await caldav_tools.add_task(
            summary="To complete", list_name="Tasks"
        )
        summary = add_result["data"]
        # Confirm task exists before completing
        before = await caldav_tools.tasks(list_name="Tasks")
        summaries_before = [t.get("summary") for t in before["data"]]
        assert "To complete" in summaries_before, "Task should exist before completing"

        comp_result = await caldav_tools.complete_task(
            summary=summary, list_name="Tasks", __user__={"timezone": "UTC"}
        )
        assert comp_result["result"] == "True"

        # Verify task is gone from tasks (completed tasks are filtered out)
        after = await caldav_tools.tasks(list_name="Tasks")
        summaries_after = [t.get("summary") for t in after["data"]]
        assert (
            summary not in summaries_after
        ), "Completed task should not appear in tasks"

    @staticmethod
    async def _seed_recurring_task(cal, summary: str, count: int = 5) -> str:
        """Create a recurring VTODO (FREQ=DAILY;COUNT=count) directly; return uid."""
        import uuid as uuid_mod

        zi = ZoneInfo("UTC")
        now = datetime.now(zi)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        dtstart = (now - timedelta(days=1)).strftime("%Y%m%dT%H%M%SZ")
        due = (now + timedelta(days=1)).strftime("%Y%m%dT%H%M%SZ")
        uid = str(uuid_mod.uuid4())
        ical = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//owuinc-test//EN\r\n"
            "BEGIN:VTODO\r\n"
            f"UID:{uid}\r\nSUMMARY:{summary}\r\nDTSTAMP:{stamp}\r\n"
            f"DTSTART:{dtstart}\r\nDUE:{due}\r\n"
            f"RRULE:FREQ=DAILY;COUNT={count}\r\n"
            "END:VTODO\r\nEND:VCALENDAR\r\n"
        )
        await cal.save_todo(ical=ical)
        return uid

    @pytest.mark.asyncio
    async def test_complete_recurring_task_continues_series(
        self, caldav_tools, tasks_calendar
    ):
        """Completing a recurring task files the done occurrence as a
        completed copy and keeps the master series alive (does NOT end it)."""
        uid = await self._seed_recurring_task(tasks_calendar, "Standup", count=5)

        result = await caldav_tools.complete_task(
            summary="Standup", list_name="Tasks", __user__={"timezone": "UTC"}
        )
        assert result["result"] == "True"
        assert "series continues" in result["data"]

        master = await tasks_calendar.todo_by_uid(uid)
        comp = master.component
        assert str(comp.get("status") or "").upper() != "COMPLETED"
        assert "COMPLETED" not in comp
        assert "RRULE" in comp
        assert int(comp["RRULE"]["COUNT"][0]) == 4, "COUNT should decrement"

        all_todos = await tasks_calendar.todos(include_completed=True)
        done_copies = [
            t
            for t in all_todos
            if str(t.component.get("summary")) == "Standup"
            and str(t.component.get("status") or "").upper() == "COMPLETED"
        ]
        assert len(done_copies) == 1, "one completed copy should be filed off"
        assert "RRULE" not in done_copies[0].component

    @pytest.mark.asyncio
    async def test_complete_recurring_task_entire_series(
        self, caldav_tools, tasks_calendar
    ):
        """entire_series=True completes the master and drops its RRULE."""
        uid = await self._seed_recurring_task(tasks_calendar, "Audit", count=3)

        result = await caldav_tools.complete_task(
            summary="Audit",
            list_name="Tasks",
            entire_series=True,
            __user__={"timezone": "UTC"},
        )
        assert result["result"] == "True"
        assert "series ended" in result["data"]

        master = await tasks_calendar.todo_by_uid(uid)
        comp = master.component
        assert str(comp.get("status") or "").upper() == "COMPLETED"
        assert "RRULE" not in comp

        all_todos = await tasks_calendar.todos(include_completed=True)
        audits = [t for t in all_todos if str(t.component.get("summary")) == "Audit"]
        assert len(audits) == 1, "no extra completed copy should be filed"

    @staticmethod
    async def _seed_todo(cal, uid: str, summary: str, related_to: str | None = None):
        """PUT a raw VTODO with RELATED-TO that has no RELTYPE parameter."""
        zi = ZoneInfo("UTC")
        stamp = datetime.now(zi).strftime("%Y%m%dT%H%M%SZ")
        rel = f"RELATED-TO:{related_to}\r\n" if related_to else ""
        ical = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//owuinc-test//EN\r\n"
            "BEGIN:VTODO\r\n"
            f"UID:{uid}\r\nSUMMARY:{summary}\r\nDTSTAMP:{stamp}\r\n{rel}"
            "END:VTODO\r\nEND:VCALENDAR\r\n"
        )
        await cal.save_todo(ical=ical)

    @pytest.mark.asyncio
    async def test_tasks_nests_link_without_reltype(self, caldav_tools, tasks_calendar):
        """RELATED-TO without a RELTYPE parameter means PARENT (RFC 5545)."""
        await self._seed_todo(tasks_calendar, "pt-parent", "Plan trip")
        await self._seed_todo(
            tasks_calendar, "pt-child", "Book hotel", related_to="pt-parent"
        )
        tree = await caldav_tools.tasks(list_name="Tasks")
        assert tree["result"] == "True"
        roots = {t["summary"]: t for t in tree["data"]}
        assert "Book hotel" not in roots, "child must not appear as a root"
        kids = [s["summary"] for s in roots["Plan trip"].get("subtasks", [])]
        assert "Book hotel" in kids

    @pytest.mark.asyncio
    async def test_edit_task_cycle_detected_through_reltype_less_link(
        self, caldav_tools, tasks_calendar
    ):
        """A->B (no RELTYPE): reparenting B under A is a cycle; the old
        ancestor walk only counted RELTYPE=PARENT links and missed it."""
        await self._seed_todo(tasks_calendar, "cyc-a", "Cyc A", related_to="cyc-b")
        await self._seed_todo(tasks_calendar, "cyc-b", "Cyc B")
        result = await caldav_tools.edit_task(
            uid="cyc-b", new_related_to="Cyc A", list_name="Tasks"
        )
        assert result["result"] == "False"
        assert "circular" in result["details"]

    @pytest.mark.asyncio
    async def test_delete_task(self, caldav_tools, tasks_calendar):
        """Verify delete_task removes a task from the list."""
        add_result = await caldav_tools.add_task(summary="To delete", list_name="Tasks")
        summary = add_result["data"]
        del_result = await caldav_tools.delete_task(summary=summary, list_name="Tasks")
        assert del_result["result"] == "True"
        tasks = await caldav_tools.tasks(list_name="Tasks")
        summaries = [t.get("summary") for t in tasks["data"]]
        assert summary not in summaries


class TestEventOperations:
    """Tests for calendar event CRUD: create, get, edit, delete."""

    @pytest_asyncio.fixture
    async def personal_calendar(self, caldav_tools):
        """Create a Personal calendar on the Radicale server."""
        client = await caldav_tools._caldav_client()
        principal = await client.principal()
        cal = await _make_calendar(principal, name="Personal", cal_id="personal")
        yield cal
        try:
            await cal.delete()
        except Exception:
            pass

    @pytest_asyncio.fixture
    async def future_event(self, caldav_tools, personal_calendar):
        """Create an event in the near future for testing."""
        zi = ZoneInfo("America/New_York")
        now = datetime.now(zi).replace(second=0, microsecond=0)
        start = (now + timedelta(hours=1)).isoformat()
        end = (now + timedelta(hours=2)).isoformat()
        result = await caldav_tools.create_calendar_event(
            summary="Test event",
            calendar_name="Personal",
            start=start,
            end=end,
            __user__={"timezone": "America/New_York"},
        )
        summary = result["data"]
        yield summary
        try:
            await caldav_tools.delete_calendar_event(
                summary=summary, calendar_name="Personal"
            )
        except Exception:
            pass

    @pytest.mark.asyncio
    async def test_create_calendar_event(self, caldav_tools, personal_calendar):
        """Verify create_calendar_event creates an event and returns its summary."""
        zi = ZoneInfo("America/New_York")
        now = datetime.now(zi).replace(second=0, microsecond=0)
        start = (now + timedelta(hours=1)).isoformat()
        end = (now + timedelta(hours=2)).isoformat()
        result = await caldav_tools.create_calendar_event(
            summary="Integration test event",
            calendar_name="Personal",
            start=start,
            end=end,
            __user__={"timezone": "America/New_York"},
        )
        assert result["result"] == "True"
        assert "data" in result
        assert result["data"] == "Integration test event"

    @pytest.mark.asyncio
    async def test_calendar_events(self, caldav_tools, future_event):
        """Verify calendar_events surfaces the future event.

        Radicale ignores server-side time-range filters, so this also
        covers the client-side window fallback (refetch + local filter).
        """
        result = await caldav_tools.calendar_events(
            calendar_name="Personal", __user__={"timezone": "America/New_York"}
        )
        assert result["result"] == "True"
        summaries = [e.get("summary") for e in result["data"]]
        assert "Test event" in summaries

    @staticmethod
    async def _seed_event(
        cal,
        uid: str,
        summary: str,
        dtstart=None,
        dtend=None,
        rrule: str | None = None,
        exdates=(),
    ):
        """PUT a raw VEVENT so uid/RRULE/EXDATE are fully controlled."""
        zi = ZoneInfo("UTC")

        def f(dt):
            return dt.strftime("%Y%m%dT%H%M%SZ")

        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//owuinc-test//EN",
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"SUMMARY:{summary}",
            f"DTSTAMP:{f(datetime.now(zi))}",
        ]
        if dtstart:
            lines.append(f"DTSTART:{f(dtstart)}")
        if dtend:
            lines.append(f"DTEND:{f(dtend)}")
        if rrule:
            lines.append(f"RRULE:{rrule}")
        for ex in exdates:
            lines.append(f"EXDATE:{f(ex)}")
        lines += ["END:VEVENT", "END:VCALENDAR", ""]
        await cal.save_event(ical="\r\n".join(lines))

    @pytest.mark.asyncio
    async def test_calendar_events_expands_recurring_with_exdate(
        self, caldav_tools, personal_calendar
    ):
        """Recurring series expands to one entry per occurrence inside the
        window, and the EXDATE instance is dropped.

        (RECURRENCE-ID overrides share the master's UID; Radicale rejects
        duplicate UIDs with 409, so override handling is unit-tested
        against the pure expander instead.)
        """
        zi = ZoneInfo("UTC")
        base = datetime.now(zi).replace(second=0, microsecond=0) + timedelta(hours=1)
        uid = "recurring-series-test"
        await self._seed_event(
            personal_calendar,
            uid,
            "Standup",
            base,
            base + timedelta(hours=1),
            rrule="FREQ=DAILY;COUNT=3",
            exdates=(base + timedelta(days=1),),
        )

        result = await caldav_tools.calendar_events(
            calendar_name="Personal", __user__={"timezone": "UTC"}
        )
        assert result["result"] == "True"
        occurrences = [e for e in result["data"] if e.get("rrule")]
        assert len(occurrences) == 2, "COUNT=3 minus one EXDATE instance"
        day2 = (base + timedelta(days=1)).strftime("%Y-%m-%d")
        assert not any(
            e["dtstart"].startswith(day2) for e in occurrences
        ), "EXDATE instance must not be returned"
        starts = sorted(e["dtstart"] for e in occurrences)
        assert starts[0] == base.isoformat()
        assert starts[1] == (base + timedelta(days=2)).isoformat()

    @pytest.mark.asyncio
    async def test_edit_calendar_event(self, caldav_tools, future_event):
        """Verify edit_calendar_event modifies an event's summary."""
        zi = ZoneInfo("America/New_York")
        now = datetime.now(zi).replace(second=0, microsecond=0)
        new_start = (now + timedelta(hours=3)).isoformat()
        edit_result = await caldav_tools.edit_calendar_event(
            summary=future_event,
            calendar_name="Personal",
            new_summary="Edited event",
            new_start=new_start,
            __user__={"timezone": "America/New_York"},
        )
        assert edit_result["result"] == "True"
        # Verify via raw cal.events() — calendar_events can't be used
        # because Radicale ignores time-range filters in search()
        cal = await _get_calendar(caldav_tools, "Personal")
        events = await cal.events()
        for e in events:
            if e.component["summary"] == "Edited event":
                break
        else:
            assert False, "Event not found after edit"

    @pytest.mark.asyncio
    async def test_delete_calendar_event(self, caldav_tools, personal_calendar):
        """Verify delete_calendar_event removes an event."""
        zi = ZoneInfo("America/New_York")
        now = datetime.now(zi).replace(second=0, microsecond=0)
        start = (now + timedelta(hours=1)).isoformat()
        end = (now + timedelta(hours=2)).isoformat()
        add_result = await caldav_tools.create_calendar_event(
            summary="To delete",
            calendar_name="Personal",
            start=start,
            end=end,
            __user__={"timezone": "America/New_York"},
        )
        summary = add_result["data"]
        del_result = await caldav_tools.delete_calendar_event(
            summary=summary, calendar_name="Personal"
        )
        assert del_result["result"] == "True"
        # Verify via raw cal.events() — calendar_events can't be used
        # because Radicale ignores time-range filters in search()
        cal = await _get_calendar(caldav_tools, "Personal")
        event_summaries = [e.component["summary"] for e in await cal.events()]
        assert summary not in event_summaries
