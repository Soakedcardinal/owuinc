"""Unit tests for decorator error handling, sanitization, and DEBUG_MODE."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from owuinc.owuinc import (
    _sanitize,
    caldav_safe,
    webdav_safe,
)


class MockValves:
    DEBUG_MODE: bool = False


class MockTools:
    valves: MockValves

    def __init__(self):
        self.valves = MockValves()


# ---------------------------------------------------------------------------
# _sanitize
# ---------------------------------------------------------------------------


class TestSanitize:
    def test_strips_url(self):
        assert _sanitize("error at https://example.com/path") == "error at <url>"
        assert _sanitize("http://host/remote.php/dav") == "<url>"

    def test_strips_uuid(self):
        assert _sanitize("uid: a1b2c3d4-e5f6-7890-abcd-ef1234567890") == "uid: <uuid>"

    def test_strips_both(self):
        msg = (
            "https://nc.example.com/remote.php/dav/files/"
            "a1b2c3d4-e5f6-7890-abcd-ef1234567890/"
        )
        result = _sanitize(msg)
        assert "a1b2c3d4" not in result
        assert "nc.example.com" not in result

    def test_preserves_plain_message(self):
        assert _sanitize("not whitelisted") == "not whitelisted"


# ---------------------------------------------------------------------------
# _safe decorator — general behavior
# ---------------------------------------------------------------------------


class TestSafeDecorator:
    @pytest.fixture
    def tool(self):
        return MockTools()

    async def test_success_returns_true(self, tool):
        @webdav_safe
        async def ok(self, x: int) -> int:
            return x + 1

        res = await ok(tool, 3)
        assert res == {"result": "True", "data": 4}

    async def test_success_no_return(self, tool):
        @webdav_safe
        async def nothing(self) -> None:
            pass

        res = await nothing(tool)
        assert res == {"result": "True"}

    async def test_connection_error_never_raises(self, tool):
        from aiowebdav2.exceptions import ConnectionExceptionError

        @webdav_safe
        async def fail(self) -> None:
            raise ConnectionExceptionError("timeout")

        res = await fail(tool)
        assert res["result"] == "False"
        assert res["details"] == "connection error"

    async def test_timeout_error_never_raises(self, tool):
        @caldav_safe
        async def fail(self) -> None:
            raise TimeoutError("timed out")

        res = await fail(tool)
        assert res["result"] == "False"
        assert res["details"] == "connection error"

    async def test_connection_error_connection_error(self, tool):
        @caldav_safe
        async def fail(self) -> None:
            raise ConnectionError("network down")

        res = await fail(tool)
        assert res["result"] == "False"
        assert res["details"] == "connection error"

    async def test_value_error_surfaces_message(self, tool):
        @webdav_safe
        async def fail(self) -> None:
            raise ValueError("Access denied")

        res = await fail(tool)
        assert res["result"] == "False"
        assert res["details"] == "Access denied"

    async def test_generic_exception_surfaces_message(self, tool):
        @caldav_safe
        async def fail(self) -> None:
            raise Exception("my custom error")

        res = await fail(tool)
        assert res["result"] == "False"
        assert res["details"] == "my custom error"

    async def test_sanitizes_urls_in_error(self, tool):
        @webdav_safe
        async def fail(self) -> None:
            raise ValueError("error at https://secret.example.com/remote.php")

        res = await fail(tool)
        assert "secret.example.com" not in res["details"]

    async def test_sanitizes_uuids_in_error(self, tool):
        @caldav_safe
        async def fail(self) -> None:
            raise Exception("uid a1b2c3d4-e5f6-7890-abcd-ef1234567890 not found")

        res = await fail(tool)
        assert "a1b2c3d4" not in res["details"]

    async def test_sync_function_works(self, tool):
        @webdav_safe
        def sync_ok(self) -> str:
            return "hello"

        res = await sync_ok(tool)
        assert res == {"result": "True", "data": "hello"}


# ---------------------------------------------------------------------------
# DEBUG_MODE behavior
# ---------------------------------------------------------------------------


class TestDebugMode:
    @pytest.fixture
    def tool(self):
        t = MockTools()
        t.valves.DEBUG_MODE = True
        return t

    async def test_connection_error_includes_type(self, tool):
        from aiowebdav2.exceptions import ConnectionExceptionError

        @webdav_safe
        async def fail(self) -> None:
            raise ConnectionExceptionError("timeout")

        res = await fail(tool)
        assert "ConnectionExceptionError" in res["details"]

    async def test_timeout_error_includes_type(self, tool):
        @caldav_safe
        async def fail(self) -> None:
            raise TimeoutError()

        res = await fail(tool)
        assert "TimeoutError" in res["details"]

    async def test_generic_error_includes_type_and_message(self, tool):
        @caldav_safe
        async def fail(self) -> None:
            raise RuntimeError("something broke")

        res = await fail(tool)
        assert res["details"] == "something broke"

    async def test_status_event_emitted_on_error(self, tool):
        from aiowebdav2.exceptions import NoConnectionError

        emitter = AsyncMock()

        @webdav_safe
        async def fail(self, __event_emitter__=None) -> None:
            raise NoConnectionError()

        await fail(tool, __event_emitter__=emitter)
        await asyncio.sleep(0.1)
        assert emitter.called

    async def test_no_status_event_without_debug(self):
        t = MockTools()
        t.valves.DEBUG_MODE = False
        emitter = AsyncMock()

        from aiowebdav2.exceptions import NoConnectionError

        @webdav_safe
        async def fail(self, __event_emitter__=None) -> None:
            raise NoConnectionError()

        await fail(t, __event_emitter__=emitter)
        await asyncio.sleep(0.1)
        assert not emitter.called

    async def test_value_error_details_surfaces_in_debug(self, tool):
        @webdav_safe
        async def fail(self) -> None:
            raise ValueError("string not found")

        res = await fail(tool)
        assert res["details"] == "string not found"

    async def test_caldav_exception_message_surfaces(self, tool):
        from caldav.lib.error import NotFoundError

        @caldav_safe
        async def fail(self) -> None:
            raise NotFoundError("calendar 'Private' not found")

        res = await fail(tool)
        assert "Private" in res["details"]
