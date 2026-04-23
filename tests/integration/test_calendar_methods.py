"""Integration tests for CalDAV methods using local Radicale server"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

logger = logging.getLogger(__name__)


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

    def test_caldav_client_is_correct_type(self, caldav_tools):
        """Verify the overridden caldav_client returns a DAVClient instance."""
        client = caldav_tools.caldav_client
        from caldav.davclient import DAVClient

        logger.info(f">>> [TEST] caldav_client type: {type(client).__name__}")
        assert isinstance(
            client, DAVClient
        ), f"Expected DAVClient, got {type(client).__name__}"

    def test_caldav_client_url_is_radicale_root(self, caldav_tools):
        """Verify the client points at Radicale root, NOT /remote.php/dav."""
        client = caldav_tools.caldav_client
        url_str = str(client.url)
        logger.info(f">>> [TEST] caldav_client.url: {url_str}")
        assert (
            url_str == "http://127.0.0.1:5232"
        ), f"Expected Radicale root URL, got {url_str}"
        assert (
            "remote.php" not in url_str
        ), f"Client URL should not contain remote.php/dav: {url_str}"

    def test_principal_discovery_works(self, caldav_tools):
        """Verify the caldav library can discover the principal against Radicale."""
        client = caldav_tools.caldav_client
        principal = client.principal()
        logger.info(
            f">>> [TEST] Principal: {principal}, type: {type(principal).__name__}"
        )
        assert principal is not None, "Principal discovery failed"

    def test_caldav_client_property_restored_after_test(self, caldav_tools):
        """Verify the override is active during test.

        Restoration happens in the fixture's finally block.
        """
        client = caldav_tools.caldav_client
        url_str = str(client.url)
        logger.info(f">>> [TEST] During test, client.url: {url_str}")
        assert (
            "5232" in url_str
        ), f"Override should be active during test, got {url_str}"
        logger.info(
            ">>> [TEST] Override is active; "
            "restoration verified in conftest teardown log"
        )


class TestGetCalendars:
    """Tests for get_calendars() tool method."""

    def test_get_calendars_returns_empty_when_no_calendars(self, caldav_tools):
        """Verify get_calendars returns empty list when no calendars exist.

        Fresh Radicale server starts with no calendars, so this tests that case.
        This is a REAL integration test against the actual Radicale server.
        """
        logger.info(">>> [TEST] === get_calendars test START ===")
        logger.info(
            ">>> [TEST] Tools instance: base_url=%s, username=%s, sandbox=%s",
            caldav_tools.valves.NEXTCLOUD_BASE_URL,
            caldav_tools.valves.NEXTCLOUD_USERNAME,
            caldav_tools.valves.SANDBOX_DIR,
        )

        # Verify the caldav_client property is wired correctly
        client = caldav_tools.caldav_client
        logger.info(f">>> [TEST] caldav_client type: {type(client).__name__}")
        logger.info(f">>> [TEST] caldav_client.url: {client.url}")

        # Call the method under test
        logger.info(">>> [TEST] Calling caldav_tools.get_calendars() ...")
        result = caldav_tools.get_calendars()
        logger.info(f">>> [TEST] Raw result: {result!r}")

        # Assert structure
        logger.info(f">>> [TEST] result['result'] = {result.get('result')!r}")
        assert result["result"] == "True", (
            f"Expected result='True', got {result.get('result')!r}. "
            f"Details: {result.get('details', 'N/A')!r}"
        )

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

        logger.info(">>> [TEST] === get_calendars test PASS ===")

    def test_get_calendars_result_has_correct_structure(self, caldav_tools):
        """Verify the caldav_safe wrapper produces the expected dict structure."""
        result = caldav_tools.get_calendars()

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


class TestGetTaskLists:
    """Tests for get_task_lists() tool method."""

    def test_get_task_lists_returns_empty_when_no_tasks_calendar(self, caldav_tools):
        """Verify get_task_lists returns empty list when no Tasks calendar exists."""
        result = caldav_tools.get_task_lists()
        assert result["result"] == "True"
        assert "data" in result
        assert result["data"] == []


class TestTaskOperations:
    """Tests for task CRUD operations.

    Covers: add_task, get_tasks, edit_task, complete_task, delete_task.
    """

    @pytest.fixture
    def tasks_calendar(self, caldav_tools):
        """Create a Tasks calendar on the Radicale server."""
        principal = caldav_tools.caldav_client.principal()
        cal = principal.make_calendar(name="Tasks", cal_id="tasks")
        yield cal
        try:
            cal.delete()
        except Exception:
            pass

    @pytest.fixture
    def personal_calendar(self, caldav_tools):
        """Create a Personal calendar on the Radicale server."""
        principal = caldav_tools.caldav_client.principal()
        cal = principal.make_calendar(name="Personal", cal_id="personal")
        yield cal
        try:
            cal.delete()
        except Exception:
            pass

    def test_get_task_lists_with_tasks_calendar(self, caldav_tools, tasks_calendar):
        """Verify get_task_lists returns the Tasks calendar when it exists."""
        result = caldav_tools.get_task_lists()
        assert result["result"] == "True"
        assert "data" in result
        assert "Tasks" in result["data"]

    def test_add_task(self, caldav_tools, tasks_calendar):
        """Verify add_task creates a task and returns its UID."""
        result = caldav_tools.add_task(summary="Test task", list_name="Tasks")
        assert result["result"] == "True"
        assert "data" in result
        assert result["data"] is not None
        assert len(result["data"]) == 36  # UUID format

    def test_get_tasks(self, caldav_tools, tasks_calendar):
        """Verify get_tasks returns tasks from the Tasks calendar."""
        caldav_tools.add_task(summary="Get tasks test", list_name="Tasks")
        result = caldav_tools.get_tasks(list_name="Tasks")
        assert result["result"] == "True"
        assert "data" in result
        assert isinstance(result["data"], list)
        assert len(result["data"]) >= 1
        summaries = [t.get("summary") for t in result["data"]]
        assert "Get tasks test" in summaries

    def test_edit_task(self, caldav_tools, tasks_calendar):
        """Verify edit_task modifies a task's summary."""
        add_result = caldav_tools.add_task(
            summary="Original summary", list_name="Tasks"
        )
        uid = add_result["data"]
        edit_result = caldav_tools.edit_task(
            uid=uid, new_summary="Edited summary", list_name="Tasks"
        )
        assert edit_result["result"] == "True"
        tasks = caldav_tools.get_tasks(list_name="Tasks")
        for t in tasks["data"]:
            if t.get("uid") == uid:
                assert t.get("summary") == "Edited summary"
                break
        else:
            assert False, f"Task with uid {uid} not found after edit"

    def test_complete_task(self, caldav_tools, tasks_calendar):
        """Verify complete_task marks a task as COMPLETED.

        Note: get_tasks filters out completed tasks (expected CalDAV behavior),
        so we can't verify via round-trip through get_tasks. We verify the
        method returns success and the task is no longer in get_tasks results.
        """
        add_result = caldav_tools.add_task(summary="To complete", list_name="Tasks")
        uid = add_result["data"]
        # Confirm task exists before completing
        before = caldav_tools.get_tasks(list_name="Tasks")
        uids_before = [t.get("uid") for t in before["data"]]
        assert uid in uids_before, "Task should exist before completing"

        comp_result = caldav_tools.complete_task(uid=uid, list_name="Tasks")
        assert comp_result["result"] == "True"

        # Verify task is gone from get_tasks (completed tasks are filtered out)
        after = caldav_tools.get_tasks(list_name="Tasks")
        uids_after = [t.get("uid") for t in after["data"]]
        assert uid not in uids_after, "Completed task should not appear in get_tasks"

    def test_delete_task(self, caldav_tools, tasks_calendar):
        """Verify delete_task removes a task from the list."""
        add_result = caldav_tools.add_task(summary="To delete", list_name="Tasks")
        uid = add_result["data"]
        del_result = caldav_tools.delete_task(uid=uid, list_name="Tasks")
        assert del_result["result"] == "True"
        tasks = caldav_tools.get_tasks(list_name="Tasks")
        uids = [t.get("uid") for t in tasks["data"]]
        assert uid not in uids


class TestEventOperations:
    """Tests for calendar event CRUD: create, get, edit, delete."""

    @pytest.fixture
    def personal_calendar(self, caldav_tools):
        """Create a Personal calendar on the Radicale server."""
        principal = caldav_tools.caldav_client.principal()
        cal = principal.make_calendar(name="Personal", cal_id="personal")
        yield cal
        try:
            cal.delete()
        except Exception:
            pass

    @pytest.fixture
    def future_event(self, caldav_tools, personal_calendar):
        """Create an event in the near future for testing."""
        zi = ZoneInfo("America/New_York")
        now = datetime.now(zi).replace(second=0, microsecond=0)
        start = (now + timedelta(hours=1)).isoformat()
        end = (now + timedelta(hours=2)).isoformat()
        result = caldav_tools.create_calendar_event(
            summary="Test event",
            calendar_name="Personal",
            start=start,
            end=end,
            __user__={"timezone": "America/New_York"},
        )
        uid = result["data"]
        yield uid
        try:
            caldav_tools.delete_calendar_event(uid=uid, calendar_name="Personal")
        except Exception:
            pass

    def test_create_calendar_event(self, caldav_tools, personal_calendar):
        """Verify create_calendar_event creates an event and returns its UID."""
        zi = ZoneInfo("America/New_York")
        now = datetime.now(zi).replace(second=0, microsecond=0)
        start = (now + timedelta(hours=1)).isoformat()
        end = (now + timedelta(hours=2)).isoformat()
        result = caldav_tools.create_calendar_event(
            summary="Integration test event",
            calendar_name="Personal",
            start=start,
            end=end,
            __user__={"timezone": "America/New_York"},
        )
        assert result["result"] == "True"
        assert "data" in result
        assert result["data"] is not None
        assert len(result["data"]) == 36  # UUID format

    def test_get_calendar_events(self, caldav_tools, future_event):
        """SKIP: Radicale returns 0 events for all time-range searches.

        get_calendar_events uses cal.search(start=datetime.now()), which
        Radicale ignores entirely — returns empty for open-ended AND closed
        ranges. Only cal.search(event=True) with no time filter works.

        This means get_calendar_events is fundamentally untestable against
        Radicale. Needs real Nextcloud or a CalDAV server that implements
        RFC 4791 time-range filtering.
        """
        pytest.skip(
            "get_calendar_events untestable — Radicale ignores time-range "
            "filters in search() (see caldav.compatibility_hints.radicale "
            "old_flags: no_search_openended)"
        )

    def test_edit_calendar_event(self, caldav_tools, future_event):
        """Verify edit_calendar_event modifies an event's summary."""
        zi = ZoneInfo("America/New_York")
        now = datetime.now(zi).replace(second=0, microsecond=0)
        new_start = (now + timedelta(hours=3)).isoformat()
        edit_result = caldav_tools.edit_calendar_event(
            uid=future_event,
            calendar_name="Personal",
            new_summary="Edited event",
            new_start=new_start,
            __user__={"timezone": "America/New_York"},
        )
        assert edit_result["result"] == "True"
        # Verify via raw cal.events() — get_calendar_events can't be used
        # because Radicale ignores time-range filters in search()
        cal = caldav_tools.caldav_client.principal().calendar(name="Personal")
        for e in cal.events():
            if e.component["uid"] == future_event:
                assert e.component["summary"] == "Edited event"
                break
        else:
            assert False, f"Event with uid {future_event} not found after edit"

    def test_delete_calendar_event(self, caldav_tools, personal_calendar):
        """Verify delete_calendar_event removes an event."""
        zi = ZoneInfo("America/New_York")
        now = datetime.now(zi).replace(second=0, microsecond=0)
        start = (now + timedelta(hours=1)).isoformat()
        end = (now + timedelta(hours=2)).isoformat()
        add_result = caldav_tools.create_calendar_event(
            summary="To delete",
            calendar_name="Personal",
            start=start,
            end=end,
            __user__={"timezone": "America/New_York"},
        )
        uid = add_result["data"]
        del_result = caldav_tools.delete_calendar_event(
            uid=uid, calendar_name="Personal"
        )
        assert del_result["result"] == "True"
        # Verify via raw cal.events() — get_calendar_events can't be used
        # because Radicale ignores time-range filters in search()
        cal = caldav_tools.caldav_client.principal().calendar(name="Personal")
        event_uids = [e.component["uid"] for e in cal.events()]
        assert uid not in event_uids
