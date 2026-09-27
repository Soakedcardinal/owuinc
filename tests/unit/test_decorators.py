"""Unit tests for decorator error handling and sanitization."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from owuinc.owuinc import (
    _sanitize,
    caldav_safe,
    webdav_safe,
)


class MockValves:
    pass


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
        msg = "https://nc.example.com/remote.php/dav/files/a1b2c3d4-e5f6-7890-abcd-ef1234567890/"
        result = _sanitize(msg)
        assert "a1b2c3d4" not in result
        assert "nc.example.com" not in result

    def test_preserves_plain_message(self):
        assert _sanitize("not whitelisted") == "not whitelisted"

    def test_redacts_literal_secrets(self):
        msg = "Basic auth failed for ncuser / sup3rsecret"
        out = _sanitize(msg, ("sup3rsecret", "ncuser"))
        assert "sup3rsecret" not in out
        assert "ncuser" not in out
        assert "<redacted>" in out

    def test_empty_secret_ignored(self):
        assert _sanitize("user ncuser", ("",)) == "user ncuser"

    def test_strips_collection_404_trailing_slash(self):
        # aiowebdav2 PROPFINDs "<path>/" for listings, so its 404 text carries a
        # slash that is not part of the requested path.
        msg = "Remote resource: owuinc/notes/missing.txt/ not found"
        assert _sanitize(msg) == "Remote resource: owuinc/notes/missing.txt not found"

    def test_keeps_slash_of_a_path_not_followed_by_not_found(self):
        assert _sanitize("listed owuinc/dir/ entries") == "listed owuinc/dir/ entries"


class TestSafeDecoratorSecrets:
    class CredValves:
        NEXTCLOUD_APP_PASSWORD = "sup3rsecret"
        NEXTCLOUD_USERNAME = "ncuser"

    class CredTools:
        def __init__(self):
            self.valves = TestSafeDecoratorSecrets.CredValves()

    async def test_error_details_redact_credentials(self):
        tool = self.CredTools()

        @webdav_safe
        async def boom(self):
            raise ValueError("auth failed: sup3rsecret")

        res = await boom(tool)
        assert res["result"] == "False"
        assert "sup3rsecret" not in res["details"]
        assert "<redacted>" in res["details"]


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
            raise ConnectionExceptionError(Exception("timeout"))

        res = await fail(tool)
        assert res["result"] == "False"
        assert res["details"] == "connection error (ConnectionExceptionError)"

    async def test_timeout_error_never_raises(self, tool):
        """A client timeout is not a plain connection error: the request
        may have completed server-side, so the details must say to verify
        before retrying (a blind retry of a timed-out write double-applies)."""

        @caldav_safe
        async def fail(self) -> None:
            raise TimeoutError("timed out")

        res = await fail(tool)
        assert res["result"] == "False"
        assert res["details"] == (
            "timed out — the request may still have completed on the server; "
            "verify before retrying"
        )

    async def test_niquests_timeout_carries_verify_hint(self, tool):
        """caldav.aio's default backend (niquests) raises its own timeout
        class; it must get the same verify-before-retrying treatment."""
        from niquests.exceptions import ReadTimeout

        @caldav_safe
        async def fail(self) -> None:
            raise ReadTimeout("Read timed out. (read timeout=30)")

        res = await fail(tool)
        assert res["result"] == "False"
        assert res["details"].startswith("Read timed out. (read timeout=30)")
        assert res["details"].endswith(
            "the request may still have completed on the server; "
            "verify before retrying"
        )

    async def test_connection_error_connection_error(self, tool):
        @caldav_safe
        async def fail(self) -> None:
            raise ConnectionError("network down")

        res = await fail(tool)
        assert res["result"] == "False"
        assert res["details"] == "connection error (ConnectionError)"

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
# Error detail surfacing
# ---------------------------------------------------------------------------


class TestErrorDetails:
    @pytest.fixture
    def tool(self):
        return MockTools()

    async def test_connection_error_includes_type(self, tool):
        from aiowebdav2.exceptions import ConnectionExceptionError

        @webdav_safe
        async def fail(self) -> None:
            raise ConnectionExceptionError(Exception("timeout"))

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
            raise NoConnectionError("oc.example")

        await fail(tool, __event_emitter__=emitter)
        await asyncio.sleep(0.1)
        assert emitter.called

    async def test_status_event_always_emitted(self):
        """Status events are user-facing and always fire."""
        t = MockTools()
        emitter = AsyncMock()

        @webdav_safe
        async def ok(self, __event_emitter__=None) -> str:
            return "hello"

        await ok(t, __event_emitter__=emitter)
        await asyncio.sleep(0.1)
        assert emitter.called

    async def test_value_error_details_surfaces(self, tool):
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
