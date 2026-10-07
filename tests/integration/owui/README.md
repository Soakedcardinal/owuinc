# Open WebUI plugin integration tests

Real Open WebUI, fake LLM. Your plugin runs inside the real server; a mock
OpenAI-compatible upstream records what Open WebUI sends it and returns scripted replies.

## Running

These tests skip unless one of these works (first available wins):

1. `OWUI_URL=http://<host>:<port> pytest tests/integration/owui` — an
   already-running instance. Its OpenAI connection must point at the mock
   (http://localhost:9999/v1); creds via OWUI_EMAIL / OWUI_PASSWORD.
2. `open-webui` installed in the dev venv (dev dependency group; needs python <3.13,
   which the project's requires-python enforces):

       uv run pytest tests/integration/owui

   The conftest starts the mock, then the server on :5234 with a throwaway DATA_DIR
   (headless admin via WEBUI_ADMIN_EMAIL/PASSWORD: admin@test.local / admin-password).
3. docker — `docker compose up` from this directory; tests drive it on :3000.

`uv run pytest` from the main dev venv runs these (via the bundled open-webui) unless
OWUI_URL is set.

## Writing a test

    def test_my_filter(owui, mock_llm, install_filter):
        install_filter("plugins/my_filter.py")           # installed active + global, removed after test
        mock_llm.script(Reply(text="hello there"))       # what the "model" answers
        owui.complete("hi")                              # inlet() runs
        assert mock_llm.last["messages"][0]["role"] == "system"    # what the model was sent
        assert owui.stream_text("hi") == "..."           # stream() output as the client sees it
        assert owui.outlet([...])["messages"][-1]["content"] == "..."   # outlet() output

    def test_my_tool(owui, mock_llm, install_tool):
        tool_id = install_tool("plugins/my_tool.py")
        mock_llm.script(Reply(tool_calls=[("method_name", {"arg": "x"})]), Reply(text="done"))
        owui.run_with_tools("prompt", [tool_id])
        tool_msg = mock_llm.requests[-1]["messages"][-1]           # role == "tool": your method's return value
        schema = mock_llm.requests[0]["tools"]                     # JSON schema generated from your hints/docstring

## What is not covered

`__event_emitter__` events (status, citations, notifications) travel over the websocket, not HTTP,
so they are not observable here. Model-attached (non-global) and user-toggleable filters are
not exercised; every filter is installed global. Tests share one server, so they run serially.
