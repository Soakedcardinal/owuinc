"""Tests for startup_context_injector pure functions."""

import pytest

import startup_context_injector
from startup_context_injector import (
    _ETAG_CACHE,
    _is_turn_start,
    _token_count,
    _try_inject,
    is_blacklisted,
    validate_path,
)


@pytest.fixture
def valves():
    """Create a mock valves object with test sandbox."""
    from pydantic import BaseModel

    class MockValves(BaseModel):
        SANDBOX_DIR: str = "owuinc"

    return MockValves()


class TestTokenCount:
    """Test _token_count helper."""

    def test_empty_string(self):
        assert _token_count("") == 0

    def test_single_word(self):
        assert _token_count("hello") == 1

    def test_short_sentence(self):
        count = _token_count("hello world")
        assert count >= 2

    def test_longer_text(self):
        text = "This is a test sentence with multiple words."
        count = _token_count(text)
        assert count > 0


class TestTryInject:
    """Test _try_inject helper."""

    def test_injects_valid_content(self):
        contexts = []
        injected_info = []
        info = _try_inject(contexts, injected_info, "test.md", "hello world")

        assert info is not None
        assert info["name"] == "test.md"
        assert info["tokens"] == _token_count("hello world")
        assert len(contexts) == 1
        assert '<file path="test.md">' in contexts[0]
        assert "</file>" in contexts[0]
        assert "hello world" in contexts[0]
        assert len(injected_info) == 1

    def test_filename_with_slash_is_a_valid_tag(self):
        """Daily logs contain '/' which is illegal in a raw tag name."""
        contexts = []
        _try_inject(contexts, [], "memory/2026-09-18.md", "log body")
        assert '<file path="memory/2026-09-18.md">' in contexts[0]
        assert "</file>" in contexts[0]

    def test_filename_metacharacters_are_sanitized(self):
        contexts = []
        _try_inject(contexts, [], 'a"><evil', "body")
        first_line = contexts[0].splitlines()[0]
        assert first_line == '<file path="aevil">'

    def test_skips_none_content(self):
        contexts = []
        injected_info = []
        info = _try_inject(contexts, injected_info, "test.md", None)

        assert info is None
        assert contexts == []
        assert injected_info == []

    def test_skips_empty_string(self):
        contexts = []
        injected_info = []
        info = _try_inject(contexts, injected_info, "test.md", "")

        assert info is None
        assert contexts == []
        assert injected_info == []

    def test_multiple_injections(self):
        contexts = []
        injected_info = []
        _try_inject(contexts, injected_info, "file1.md", "content one")
        _try_inject(contexts, injected_info, "file2.md", "content two")

        assert len(contexts) == 2
        assert len(injected_info) == 2
        assert injected_info[0]["name"] == "file1.md"
        assert injected_info[1]["name"] == "file2.md"


class TestValidatePath:
    """Test validate_path for startup_context_injector."""

    def test_basic_path(self, valves):
        assert validate_path("AGENTS.md", valves) == "owuinc/AGENTS.md"

    def test_sandbox_dir_with_trailing_slash(self):
        from pydantic import BaseModel

        class MockValves(BaseModel):
            SANDBOX_DIR: str = "owuinc/"

        assert validate_path("AGENTS.md", MockValves()) == "owuinc/AGENTS.md"

    def test_path_traversal_blocked(self, valves):
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path("../etc/passwd", valves)

    def test_encoded_traversal_blocked(self, valves):
        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path("%2e%2e%2fetc", valves)

    def test_empty_sandbox_dir(self):
        """Test that empty SANDBOX_DIR still produces valid paths."""
        from pydantic import BaseModel

        class MockValves(BaseModel):
            SANDBOX_DIR: str = ""

        v = MockValves()
        result = validate_path("AGENTS.md", v)
        # With empty sandbox, prefix is "/"
        assert result == "/AGENTS.md"

    def test_empty_sandbox_dir_no_traversal(self):
        """Verify path traversal is still blocked even with empty SANDBOX_DIR."""
        from pydantic import BaseModel

        class MockValves(BaseModel):
            SANDBOX_DIR: str = ""

        with pytest.raises(Exception, match="traversal not allowed"):
            validate_path("../etc/passwd", MockValves())


class TestRequestTimeoutValveDefaults:
    """Verify REQUEST_TIMEOUT valve has correct defaults and constraints."""

    def test_request_timeout_default(self):
        f = startup_context_injector.Filter()
        assert f.valves.REQUEST_TIMEOUT == 10

    def test_request_timeout_in_range(self):
        f = startup_context_injector.Filter()
        for val in [1, 30, 120]:
            f.valves.REQUEST_TIMEOUT = val
            assert f.valves.REQUEST_TIMEOUT == val


class TestMergeSystem:
    """Test _merge_system: never discards the existing prompt, idempotent."""

    def test_no_existing_prompt(self):
        f = startup_context_injector.Filter()
        out = f._merge_system("", "INJECTED")
        assert out == (
            "<!-- owuinc:context:begin -->\n" "INJECTED\n" "<!-- owuinc:context:end -->"
        )

    def test_preserves_existing_prompt_after(self):
        f = startup_context_injector.Filter()
        out = f._merge_system("You are helpful.", "INJECTED")
        assert out.startswith("You are helpful.")
        assert "INJECTED" in out

    def test_preserves_existing_prompt_before(self):
        f = startup_context_injector.Filter()
        f.valves.INJECT_POSITION = "before"
        out = f._merge_system("You are helpful.", "INJECTED")
        assert out.index("INJECTED") < out.index("You are helpful.")

    def test_idempotent_second_call(self):
        """A block left by a previous call is replaced, not stacked."""
        f = startup_context_injector.Filter()
        once = f._merge_system("You are helpful.", "INJECTED")
        twice = f._merge_system(once, "INJECTED")
        assert twice == once

    def test_stale_block_replaced(self):
        f = startup_context_injector.Filter()
        once = f._merge_system("You are helpful.", "OLD")
        updated = f._merge_system(once, "NEW")
        assert "OLD" not in updated
        assert "NEW" in updated
        assert updated.count("owuinc:context:begin") == 1


class TestRequestMerge:
    """Test request() merging behavior and the background-task guard."""

    async def test_task_body_skips_injection(self):
        f = startup_context_injector.Filter()
        body = {
            "messages": [{"role": "user", "content": "hi"}],
            "metadata": {"task": "title"},
        }
        out = await f.request(body)
        assert out is body
        assert len(body["messages"]) == 1

    async def test_request_merges_into_existing_system(self, monkeypatch):
        f = startup_context_injector.Filter()

        async def fake_build(user=None):
            return ["INJECTED"], [{"name": "x", "tokens": 1}]

        monkeypatch.setattr(f, "_build_context", fake_build)
        body = {"messages": [{"role": "system", "content": "You are helpful."}]}
        out = await f.request(body)
        content = out["messages"][0]["content"]
        assert "You are helpful." in content
        assert "INJECTED" in content

    async def test_request_inserts_system_when_absent(self, monkeypatch):
        f = startup_context_injector.Filter()

        async def fake_build(user=None):
            return ["INJECTED"], [{"name": "x", "tokens": 1}]

        monkeypatch.setattr(f, "_build_context", fake_build)
        body = {"messages": [{"role": "user", "content": "hi"}]}
        out = await f.request(body)
        assert out["messages"][0]["role"] == "system"
        assert "INJECTED" in out["messages"][0]["content"]


class TestIsTurnStart:
    """_is_turn_start gates status to the first provider call of a turn."""

    def test_user_last_message_is_turn_start(self):
        assert _is_turn_start([{"role": "system"}, {"role": "user"}])

    def test_tool_result_is_continuation(self):
        msgs = [
            {"role": "user"},
            {"role": "assistant", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "tool_call_id": "1"},
        ]
        assert not _is_turn_start(msgs)

    def test_assistant_last_message_is_continuation(self):
        assert not _is_turn_start([{"role": "user"}, {"role": "assistant"}])

    def test_empty_messages(self):
        assert not _is_turn_start([])


class TestRequestStatusEmission:
    """Status events fire once per turn, not once per provider call."""

    @staticmethod
    def _filter(monkeypatch):
        f = startup_context_injector.Filter()

        async def fake_build(user=None):
            return ["INJECTED"], [{"name": "x", "tokens": 1}]

        monkeypatch.setattr(f, "_build_context", fake_build)
        return f

    @staticmethod
    def _emitter(events):
        async def emitter(event):
            events.append(event)

        return emitter

    async def test_emits_on_turn_start(self, monkeypatch):
        f = self._filter(monkeypatch)
        events = []
        body = {"messages": [{"role": "user", "content": "hi"}]}
        await f.request(body, __event_emitter__=self._emitter(events))
        assert any("Context injected" in e["data"]["description"] for e in events)

    async def test_silent_on_tool_continuation(self, monkeypatch):
        f = self._filter(monkeypatch)
        events = []
        body = {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "tool_calls": [{"id": "1"}]},
                {"role": "tool", "tool_call_id": "1", "content": "{}"},
            ]
        }
        out = await f.request(body, __event_emitter__=self._emitter(events))
        assert events == []
        assert "INJECTED" in out["messages"][0]["content"]

    async def test_error_emits_only_on_turn_start(self, monkeypatch):
        f = startup_context_injector.Filter()

        async def boom(user=None):
            raise RuntimeError("webdav down")

        monkeypatch.setattr(f, "_build_context", boom)

        events = []
        await f.request(
            {"messages": [{"role": "user"}]},
            __event_emitter__=self._emitter(events),
        )
        assert any("failed" in e["data"]["description"] for e in events)

        cont_events = []
        await f.request(
            {
                "messages": [
                    {"role": "user"},
                    {"role": "assistant", "tool_calls": [{"id": "1"}]},
                    {"role": "tool", "tool_call_id": "1"},
                ]
            },
            __event_emitter__=self._emitter(cont_events),
        )
        assert cont_events == []


class TestInjectorBlacklist:
    def test_exact_and_prefix_match(self):
        assert is_blacklisted("SOUL.md,memory", "SOUL.md")
        assert is_blacklisted("SOUL.md,memory", "memory/2026-09-18.md")
        assert not is_blacklisted("SOUL.md", "MEMORY.md")
        assert not is_blacklisted("secret", "mysecret/x")
        assert not is_blacklisted("", "anything")

    def test_plan_skips_blacklisted(self):
        f = startup_context_injector.Filter()
        f.valves.FILES_TO_INJECT = "AGENTS.md,SOUL.md,MEMORY.md"
        f.valves.FILE_BLACKLIST = "SOUL.md,memory"
        f.valves.INJECT_TODAY = True
        labels = [name for name, _ in f._plan_file_paths()]
        assert labels == ["AGENTS.md", "MEMORY.md"]

    def test_plan_includes_daily_after_memory(self):
        f = startup_context_injector.Filter()
        f.valves.FILES_TO_INJECT = "MEMORY.md"
        f.valves.INJECT_TODAY = True
        labels = [name for name, _ in f._plan_file_paths()]
        assert len(labels) == 2
        assert labels[0] == "MEMORY.md"
        assert labels[1].startswith("memory/")

    def test_plan_daily_uses_user_timezone(self):
        f = startup_context_injector.Filter()
        f.valves.FILES_TO_INJECT = ""
        f.valves.INJECT_TODAY = True
        labels = [
            name for name, _ in f._plan_file_paths({"timezone": "Pacific/Kiritimati"})
        ]
        assert len(labels) == 1


class TestEtagConditionalDownload:
    class Resp:
        def __init__(self, status, body=b"", etag=None):
            self.status = status
            self._body = body
            self.headers = {"ETag": etag} if etag else {}

        async def read(self):
            return self._body

    class FakeClient:
        def __init__(self):
            self.calls = []

        def get_url(self, p):
            return "http://srv/base"

        async def execute_request(self, action, path, headers_ext=None):
            self.calls.append(dict(headers_ext or {}))
            if len(self.calls) == 1:
                return TestEtagConditionalDownload.Resp(200, b"hello", '"E1"')
            if (headers_ext or {}).get("If-None-Match") == '"E1"':
                return TestEtagConditionalDownload.Resp(304)
            return TestEtagConditionalDownload.Resp(200, b"changed", '"E2"')

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        _ETAG_CACHE.clear()
        yield
        _ETAG_CACHE.clear()

    async def test_second_call_uses_if_none_match_and_304_cache(self):
        f = startup_context_injector.Filter()
        client = self.FakeClient()
        assert await f._download_file(client, "AGENTS.md") == "hello"
        assert await f._download_file(client, "AGENTS.md") == "hello"
        assert client.calls[0] == {}
        assert client.calls[1] == {"If-None-Match": '"E1"'}

    async def test_missing_file_returns_none_and_pops_cache(self):
        from aiowebdav2.exceptions import RemoteResourceNotFoundError

        f = startup_context_injector.Filter()

        class GoneClient(self.FakeClient):
            async def execute_request(self, action, path, headers_ext=None):
                raise RemoteResourceNotFoundError(path=path)

        assert await f._download_file(GoneClient(), "AGENTS.md") is None
        assert _ETAG_CACHE == {}


class TestInjectorValveAdditions:
    def test_priority_and_blacklist_defaults(self):
        f = startup_context_injector.Filter()
        assert f.valves.priority == 0
        assert f.valves.FILE_BLACKLIST == ""


class TestRequestPassesUser:
    async def test_user_forwarded_to_build_context(self, monkeypatch):
        f = startup_context_injector.Filter()
        seen = {}

        async def fake_build(user=None):
            seen["user"] = user
            return ["INJECTED"], [{"name": "x", "tokens": 1}]

        monkeypatch.setattr(f, "_build_context", fake_build)
        await f.request({"messages": []}, __user__={"timezone": "UTC"})
        assert seen["user"] == {"timezone": "UTC"}
