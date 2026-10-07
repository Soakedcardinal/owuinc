from .mock_llm import Reply

PLUGIN = "plugins/reverse_tool.py"


def test_tool_is_offered_with_schema_and_result_reaches_llm(
    owui, mock_llm, install_tool
):
    tool_id = install_tool(PLUGIN)
    mock_llm.script(
        Reply(
            tool_calls=[("reverse_string", {"string": "abc"})]
        ),  # 1st upstream call: "model" calls the tool
        Reply(text="It is cba"),  # 2nd upstream call: final answer
    )

    msg = owui.run_with_tools("reverse abc", tool_ids=[tool_id])

    first, last = mock_llm.requests[0], mock_llm.requests[-1]
    assert len(mock_llm.requests) >= 2

    # 1) Schema generated from type hints + docstring
    fn = {t["function"]["name"]: t["function"] for t in first["tools"]}[
        "reverse_string"
    ]
    assert fn["parameters"]["properties"]["string"]["type"] == "string"
    assert fn["parameters"]["required"] == ["string"]
    assert "Reverses the input string" in fn["description"]

    # 2) The tool actually ran and its output was fed back to the LLM
    tool_msgs = [m for m in last["messages"] if m["role"] == "tool"]
    assert tool_msgs and "cba" in tool_msgs[-1]["content"]

    # 3) Final answer landed in the chat record
    assert "It is cba" in msg["content"]
