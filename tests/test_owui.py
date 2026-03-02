import inspect
import json
import os
import re
import time
import uuid
from typing import cast

import dotenv
import requests


def load_required_env() -> tuple[str, str, str, str]:
    """Load and validate all required environment variables."""
    dotenv.load_dotenv(dotenv.find_dotenv())
    required = {
        "URL": os.getenv("URL"),
        "KEY": os.getenv("KEY"),
        "USER_ID": os.getenv("USER_ID"),
        "FOLDER_ID": os.getenv("FOLDER_ID"),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        found = {k: v for k, v in required.items() if v}
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            f"Found: {found}"
        )
    return cast(
        tuple[str, str, str, str],
        (
            required["URL"],
            required["KEY"],
            required["USER_ID"],
            required["FOLDER_ID"],
        ),
    )


URL, KEY, USER_ID, FOLDER_ID = load_required_env()
header = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
header_2 = {"Authorization": f"Bearer {KEY}"}
preamble = """### TASK
Run the test and verify the results are as expected.
Stop immediately if anything unexpected happens.
Output **ONLY** "pass" or "fail".

### TEST
"""


# https://docs.openwebui.com/tutorials/integrations/backend-controlled-ui-compatible-flow
def create_chat(prompt, title, tool_ids=["owuinc"], model="owuinc") -> str:
    ts = int(time.time())
    ts2 = ts + 1000

    user_msg_id = str(uuid.uuid4())
    assistant_msg_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    # prepend preamble to prompt
    full_prompt = preamble + prompt

    # create chat
    payload = {
        "chat": {
            "title": title,
            "user_id": USER_ID,
            "models": [model],
            "messages": [
                {
                    "id": user_msg_id,
                    "role": "user",
                    "content": full_prompt,
                    "timestamp": ts,
                    "models": [model],
                },
                {
                    "id": assistant_msg_id,
                    "role": "assistant",
                    "content": "",
                    "parentId": user_msg_id,
                    "modelName": model,
                    "modelIdx": 0,
                    "timestamp": ts2,
                },
            ],
            "history": {
                "current_id": assistant_msg_id,
                "messages": {
                    user_msg_id: {
                        "id": user_msg_id,
                        "role": "user",
                        "content": full_prompt,
                        "timestamp": ts,
                        "models": [model],
                    },
                    assistant_msg_id: {
                        "id": assistant_msg_id,
                        "role": "assistant",
                        "content": "",
                        "parentId": user_msg_id,
                        "modelName": model,
                        "modelIdx": 0,
                        "timestamp": ts2,
                    },
                },
            },
        },
        "folder_id": FOLDER_ID,
    }
    resp = requests.post(
        f"{URL}/v1/chats/new",
        headers=header,
        data=json.dumps(payload),
    )
    assert resp.ok, f"creating chat failed. Status Code: {resp.status_code}"
    chat_id = resp.json()["id"]
    assert chat_id, "creating chat failed. no chat id"
    chat_endpoint = f"{URL}/v1/chats/{chat_id}"

    # trigger completion
    completion_payload = {
        "chat_id": chat_id,
        "id": assistant_msg_id,
        "messages": [{"role": "user", "content": full_prompt}],
        "model": model,
        "stream": True,
        "background_tasks": {
            "title_generation": False,
            "tags_generation": False,
            "follow_up_generation": False,
        },
        "features": {
            "code_interpreter": False,
            "web_search": False,
            "image_generation": False,
            "memory": False,
        },
        "session_id": session_id,
        "tool_ids": tool_ids,
    }
    resp = requests.post(
        f"{URL}/chat/completions",
        headers=header,
        data=json.dumps(completion_payload),
        stream=True,
    )
    assert resp.ok, f"trigger completion failed. Status Code: {resp.status_code}"

    # wait for assistant completion
    start = time.time()
    while time.time() - start < 240:
        print("wait for completion...")
        resp = requests.get(chat_endpoint, headers=header_2)
        # Find the assistant message
        msg = (
            resp.json()
            .get("chat", {})
            .get("history", {})
            .get("messages", {})
            .get(assistant_msg_id, {})
            .get("content", "")
        )
        if msg:
            break
        time.sleep(3)
    else:
        raise TimeoutError("Assistant response timeout")
    assert msg, "assistant completion failed. no msg"

    # mark as completed
    completed_payload = {
        "chat_id": chat_id,
        "id": assistant_msg_id,
        "session_id": session_id,
        "model": model,
    }
    resp = requests.post(
        f"{URL}/chat/completed",
        headers=header,
        data=json.dumps(completed_payload),
    )
    assert resp.ok, f"mark completed failed. Status Code: {resp.status_code}"

    # get final chat
    resp = requests.get(chat_endpoint, headers=header_2)
    assert resp.ok, f"get final chat failed. Status Code: {resp.status_code}"
    # extract msg
    msg = (
        resp.json()
        .get("chat", {})
        .get("history", {})
        .get("messages", {})
        .get(assistant_msg_id, {})
        .get("content", "")
    )
    assert msg, "get final chat failed. no msg"
    msg = re.sub(r"<details[^>]*>.*?</details>", "", msg, flags=re.DOTALL)
    return msg


def pass_assert(text):
    """Assert that response contains 'pass' and not 'fail'."""
    assert "fail" not in text
    assert "pass" in text


def test_mv_file():
    test_name = inspect.currentframe().f_code.co_name
    setup_prompt = """
1. rm(paths=['foo.txt']): \\
    {"result": "False","details":"rm: not found"} \\
    OR {"result": "True"}
2. rm(paths=['bar.txt']): \\
    {"result": "False","details":"rm: not found"} \\
    OR {"result": "True"}"""
    text = create_chat(setup_prompt, f"{test_name} setup").strip()
    pass_assert(text)

    prompt = """
1. `write_file(path='foo.txt', content='lorem')`: True
2. `mv(src='foo.txt', dst='bar.txt')`: True
3. `cat(path='foo.txt')`: False
4. `cat(path='bar.txt')`: contains 'lorem'"""
    text = create_chat(prompt, f"{test_name}").strip()
    pass_assert(text)


def test_cp_file():
    test_name = inspect.currentframe().f_code.co_name
    setup_prompt = """
1. rm(paths=['foo.txt']): \\
    {"result": "False","details":"rm: not found"} \\
    OR {"result": "True"}
2. rm(paths=['bar.txt']): \\
    {"result": "False","details":"rm: not found"} \\
    OR {"result": "True"}"""
    text = create_chat(setup_prompt, f"{test_name} setup").strip()
    pass_assert(text)

    prompt = """
1. `write_file(path='foo.txt', content='lorem')`: True
2. `cp(src='foo.txt', dst='bar.txt)`: True
3. `cat(path='foo.txt')`: contains 'lorem'
4. `cat(path='bar.txt')`: contains 'lorem'"""
    text = create_chat(prompt, f"{test_name}").strip()
    pass_assert(text)


def test_mv_dir():
    test_name = inspect.currentframe().f_code.co_name
    setup_prompt = """
1. rm(paths=['foo']): \\
    {"result": "False","details":"rm: not found"} \\
    OR {"result": "True"}
2. rm(paths=['bar']): \\
    {"result": "False","details":"rm: not found"} \\
    OR {"result": "True"}"""
    text = create_chat(setup_prompt, f"{test_name} setup").strip()
    pass_assert(text)

    prompt = """
1. `mkdir(path='foo')`: True
2. `mv(src='foo', dst='bar')`: True
3. `ls()`: does not contain 'foo' and contains 'bar'"""
    text = create_chat(prompt, f"{test_name}").strip()
    pass_assert(text)


def test_cp_dir():
    test_name = inspect.currentframe().f_code.co_name
    setup_prompt = """
1. rm(paths=['foo']): \\
    {"result": "False","details":"rm: not found"} \\
    OR {"result": "True"}
2. rm(paths=['bar']): \\
    {"result": "False","details":"rm: not found"} \\
    OR {"result": "True"}"""
    text = create_chat(setup_prompt, f"{test_name} setup").strip()
    pass_assert(text)

    prompt = """
1. `mkdir(path='foo')`: True
2. `cp(src='foo', dst='bar')`: True
3. `ls()`: contains 'foo' and 'bar'"""
    text = create_chat(prompt, f"{test_name}").strip()
    pass_assert(text)


def test_append_file():
    test_name = inspect.currentframe().f_code.co_name
    setup_prompt = """
1. rm(paths=['foo.txt']): \\
    {"result": "False","details":"rm: not found"} \\
    OR {"result": "True"}"""
    text = create_chat(setup_prompt, f"{test_name} setup").strip()
    pass_assert(text)

    prompt = """
1. `write_file(path='foo.txt', content='lorem')`: True
2. `append_file(path='foo.txt',content=' ipsum')`: True
3. `cat(path='foo.txt')`: contains 'lorem ipsum'"""
    text = create_chat(prompt, f"{test_name}").strip()
    pass_assert(text)


def test_path_traversal():
    test_name = inspect.currentframe().f_code.co_name
    prompt = """
1. `ls(path=..)`: False
2. `ls(path=%2e%2e%2f)`: False
3. `ls(path=%252e%252e%252f)`: False
4. `ls()`: does not contain 'test_file_7418.txt'
5. `write_file(path='../foo.txt', content='lorem')`: False
6. `cat(path='../foo.txt')`: False
7. `rm(paths=['../foo.txt'])`: False"""
    text = create_chat(prompt, f"{test_name}").strip()
    pass_assert(text)


def test_get_tasks():
    test_name = inspect.currentframe().f_code.co_name
    prompt = """
1. `get_tasks()`: Empty"""
    text = create_chat(prompt, f"{test_name} setup").strip()
    pass_assert(text)

    prompt = """
1. `add_task(summary='foo')`: returns a UID
2. `get_tasks()`: contains the UID from step 1"""
    text = create_chat(prompt, f"{test_name}").strip()
    pass_assert(text)

    prompt = "delete_task(summary='foo'): True"
    text = create_chat(prompt, f"{test_name} cleanup").strip()
    pass_assert(text)


def test_add_task():
    test_name = inspect.currentframe().f_code.co_name
    prompt = """
1. `get_tasks()`: Empty"""
    text = create_chat(prompt, f"{test_name} setup").strip()
    pass_assert(text)

    prompt = """
1. `add_task(summary='foo')`: returns a UID
2. `get_tasks()`: contains the UID from step 1"""
    text = create_chat(prompt, f"{test_name}").strip()
    pass_assert(text)

    prompt = "delete_task(summary='foo'): True"
    text = create_chat(prompt, f"{test_name} cleanup").strip()
    pass_assert(text)


def test_edit_task():
    test_name = inspect.currentframe().f_code.co_name
    prompt = """
1. `get_tasks()`: Empty"""
    text = create_chat(prompt, f"{test_name} setup").strip()
    pass_assert(text)

    prompt = """
1. `add_task(summary='foo')`: returns a UID
2. `edit_task(uid=<uid from step 1>, \\
    new_summary='bar')`: True
3. `get_tasks()`: contains task with summary='bar'"""
    text = create_chat(prompt, f"{test_name}").strip()
    pass_assert(text)

    prompt = "`delete_task(summary='bar')`: True"
    text = create_chat(prompt, f"{test_name} cleanup").strip()
    pass_assert(text)


def test_delete_task():
    test_name = inspect.currentframe().f_code.co_name
    prompt = """
1. `get_tasks()`: Empty"""
    text = create_chat(prompt, f"{test_name} setup").strip()
    pass_assert(text)

    prompt = """
1. `add_task(summary='foo')`: returns a UID
2. `delete_task(summary='foo')`: True"""
    text = create_chat(prompt, f"{test_name}").strip()
    pass_assert(text)


def test_create_calendar_event():
    test_name = inspect.currentframe().f_code.co_name
    setup_prompt = """`delete_calendar_event(summary='foo')` returns
`{"result": "True"}` or
`{"result": "False", "details": "match not found for 'foo'"
}`"""
    text = create_chat(setup_prompt, f"{test_name} setup").strip()
    pass_assert(text)

    prompt = "`create_calendar_event(summary='foo')`: returns a UID"
    text = create_chat(prompt, f"{test_name}").strip()
    pass_assert(text)

    cleanup_prompt = "`delete_calendar_event(summary='foo')`: True"
    text = create_chat(cleanup_prompt, f"{test_name} setup").strip()
    pass_assert(text)


def test_get_calendar_events():
    test_name = inspect.currentframe().f_code.co_name
    setup_prompt = """`delete_calendar_event(summary='foo')` returns
`{"result": "True"}` or
`{"result": "False", "details": "match not found for 'foo'"
}`"""
    text = create_chat(setup_prompt, f"{test_name} setup").strip()
    pass_assert(text)

    prompt = """
1. `create_calendar_event(summary='foo')`: returns a UID
2. `get_calendar_events()`: contains UID from step 1"""
    text = create_chat(prompt, f"{test_name}").strip()
    pass_assert(text)

    cleanup_prompt = "delete_calendar_event(summary='foo'): True"
    text = create_chat(cleanup_prompt, f"{test_name} cleanup").strip()
    pass_assert(text)


def test_edit_calendar_event():
    test_name = inspect.currentframe().f_code.co_name
    setup_prompt = """`delete_calendar_event(summary='foo')` returns
`{"result": "True"}` or
`{"result": "False", "details": "match not found for 'foo'"
}`"""
    text = create_chat(setup_prompt, f"{test_name} setup").strip()
    pass_assert(text)

    prompt = """
1. `create_calendar_event(summary='foo')`: returns a UID
2. `edit_calendar_event(uid=<uid from step 1>, \\
    new_summary='bar')`: True
3. `get_calendar_events()`: contains event with summary='bar'"""
    text = create_chat(prompt, f"{test_name}").strip()
    pass_assert(text)

    cleanup_prompt = "delete_calendar_event(summary='bar'): True"
    text = create_chat(cleanup_prompt, f"{test_name} cleanup").strip()
    pass_assert(text)


def test_delete_calendar_event():
    test_name = inspect.currentframe().f_code.co_name
    setup_prompt = """`delete_calendar_event(summary='foo')` returns
`{"result": "True"}` or
`{"result": "False", "details": "match not found for 'foo'"
}`"""
    text = create_chat(setup_prompt, f"{test_name} setup").strip()
    pass_assert(text)

    prompt = """
1. `create_calendar_event(summary='foo')`: returns a UID
2. `delete_calendar_event(summary='foo')`: True"""
    text = create_chat(prompt, f"{test_name}").strip()
    pass_assert(text)


# todo depends on get_current_time
def test_event_timing():
    test_name = inspect.currentframe().f_code.co_name
    setup_prompt = """
1. `delete_calendar_event(summary='foo')` returns `{"result": "True"}` or
`{"result": "False", "details": "match not found for 'foo'"
}`"""
    text = create_chat(setup_prompt, f"{test_name} setup").strip()
    pass_assert(text)

    tool_ids = ["owuinc", "get_current_time"]
    prompt = """
1. `get_current_time`: returns time
2. `create_calendar_event(summary='foo', start=<tomorrow at 9AM>)`: returns a uid
3. `get_calendar_events()`: contains an event with summary 'foo' \\
and start time tomorrow at 9AM
4. `edit_calendar_event(uid='<uid from step 2>', \\
new_start=<the day after tomorrow at 2PM>, \\
new_end=<the day after tomorrow at 2PM>)`: True
5. `get_calendar_events()`: contains an event with the updated start time
6. `delete_calendar_event(uid='<uid from step 2>')`: True"""
    text = create_chat(prompt, f"{test_name}", tool_ids).strip()
    pass_assert(text)
