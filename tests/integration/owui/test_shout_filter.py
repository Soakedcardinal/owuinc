from .mock_llm import Reply

PLUGIN = "plugins/shout_filter.py"


def test_inlet_injects_system_prompt(owui, mock_llm, install_filter):
    install_filter(PLUGIN)
    owui.complete("hello")
    # mock_llm saw the request AFTER the filter ran
    sent = mock_llm.last["messages"]
    assert any(
        m["role"] == "system" and m["content"] == "Answer in capital letters."
        for m in sent
    )


def test_stream_uppercases_chunks(owui, mock_llm, install_filter):
    install_filter(PLUGIN)
    mock_llm.script(Reply(text="hello there world"))
    assert owui.stream_text("hi").strip() == "HELLO THERE WORLD"


def test_outlet_appends_marker(owui, install_filter):
    install_filter(PLUGIN)
    out = owui.outlet(
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
    )
    assert out["messages"][-1]["content"] == "hello\n[checked]"
