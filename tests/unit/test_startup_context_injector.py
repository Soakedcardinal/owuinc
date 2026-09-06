"""Tests for startup_context_injector pure functions."""

import pytest

import startup_context_injector
from startup_context_injector import (
    _token_count,
    _try_inject,
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
        assert "<test.md>" in contexts[0]
        assert "</test.md>" in contexts[0]
        assert "hello world" in contexts[0]
        assert len(injected_info) == 1

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
