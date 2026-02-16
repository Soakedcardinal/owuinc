import pytest
import json
import os
import re
import uuid
import time
import dotenv
import requests
import inspect


def load_required_env() -> tuple[str, str, str, str]:
    """Load and validate all required environment variables."""
    dotenv.load_dotenv(dotenv.find_dotenv())
    required = {
        "URL": os.getenv("URL"),
        "KEY": os.getenv("KEY"),
        "USER_ID": os.getenv("USER_ID"),
        "FOLDER_ID": os.getenv("FOLDER_ID")
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            f"Found: {k: v for k, v in required.items() if v}"
        )
    return (required["URL"], required["KEY"],
            required["USER_ID"], required["FOLDER_ID"])

URL, KEY, USER_ID, FOLDER_ID = load_required_env()
header = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
header_2 = {"Authorization": f"Bearer {KEY}"}
preamble = """Run the test immediately by invoking the specified tool and verify the results.

Stop immediately if errors occur.

**STRICT OUTPUT CONSTRAINTS:**
*   Output **ONLY** the strings "pass" or "fail".
*   The response must be mutually exclusive to any other text.
*   No preamble, no postscript, no partial sentences."""


def create_chat(prompt, title="owuinc test chat", tool_ids=["owuinc"], model="owuinc-test") -> str:
    """https://docs.openwebui.com/tutorials/integrations/backend-controlled-ui-compatible-flow"""
    ts = int(time.time())
    ts2=ts + 1000

    user_msg_id = str(uuid.uuid4())
    assistant_msg_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

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
                    "content": prompt,
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
                        "content": prompt,
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

    print("creating chat...")
    resp = requests.post(
        f"{URL}/v1/chats/new",
        headers=header,
        data=json.dumps(payload),
    )
    assert resp.ok, f"creating chat failed. Status Code: {resp.status_code}"
    chat_id = resp.json()["id"]

    assert chat_id, f"creating chat failed. no chat id"
    print(f"chat id: {chat_id!r}")
    chat_endpoint = f"{URL}/v1/chats/{chat_id}"

    # trigger completion
    completion_payload = {
        "chat_id": chat_id,
        "id": assistant_msg_id,
        "messages": [{"role": "user", "content": prompt}],
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
    print("trigger completion...")
    resp = requests.post(
        f"{URL}/chat/completions",
        headers=header,
        data=json.dumps(completion_payload),
        stream=True
    )
    assert resp.ok, f"trigger completion failed. Status Code: {resp.status_code}"

    # wait for assistant completion
    start = time.time()
    while time.time() - start < 60:
        print(f"waiting for assistant completion...")
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
            print("got msg")
            break
        time.sleep(2)
    else:
        raise TimeoutError("Assistant response timeout")
    assert msg, f"assistant completion failed. no msg"

    # mark as completed
    completed_payload = {
        "chat_id": chat_id,
        "id": assistant_msg_id,
        "session_id": session_id,
        "model": model,
    }
    print("Complete Assistant Message...")
    resp = requests.post(
        f"{URL}/chat/completed",
        headers=header,
        data=json.dumps(completed_payload),
    )
    assert resp.ok, f"mark completed failed. Status Code: {resp.status_code}"

    # get final chat
    print("get final chat...")
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
    assert msg, f"get final chat failed. no msg"
    msg = re.sub(r"<details[^>]*>.*?</details>", "", msg, flags=re.DOTALL)
    return msg


def print_sep():
    print("----------------------------")


def print_resp(text):
    print_sep()
    print(text)


def test_mv_file():
    test_name = inspect.currentframe().f_code.co_name
    setup_prompt = (
        preamble
        + """
    1. rm(paths=['foo.txt']): \
        {"result": "False","details":"rm: not found"} \
            OR {"result": "True"}
    2. rm(paths=['bar.txt']): \
        {"result": "False","details":"rm: not found"} \
            OR {"result": "True"}"""
    )
    print("sending setup prompt...")
    text = create_chat(setup_prompt, f"{test_name} setup").strip()
    print_resp(text)
    assert "fail" not in text, "files_setup: 'fail' found in response"
    assert "pass" in text, "files_setup: 'pass' not found in response"

    prompt = (
        preamble
        + """
    1. `write_file(path='foo.txt', content='lorem')`: True
    2. `mv(src='foo.txt', dst='bar.txt')`: True
    3. `cat(path='foo.txt')`: False
    4. `cat(path='bar.txt')`: contains 'lorem'"""
    )
    text = create_chat(prompt, f"{test_name}").strip()
    print_resp(text)
    assert "fail" not in text
    assert "pass" in text


def test_cp_file():
    test_name = inspect.currentframe().f_code.co_name
    setup_prompt = (
        preamble
        + """
    1. rm(paths=['foo.txt']): \
        {"result": "False","details":"rm: not found"} \
            OR {"result": "True"}
    2. rm(paths=['bar.txt']): \
        {"result": "False","details":"rm: not found"} \
            OR {"result": "True"}"""
    )
    print("sending setup prompt...")
    text = create_chat(setup_prompt, f"{test_name} setup").strip()
    print_resp(text)
    assert "fail" not in text, "files_setup: 'fail' found in response"
    assert "pass" in text, "files_setup: 'pass' not found in response"

    prompt = (
        preamble
        + """
    1. `write_file(path='foo.txt', content='lorem')`: True
    2. `cp(src='foo.txt', dst='bar.txt)`: True
    3. `cat(path='foo.txt')`: contains 'lorem'
    4. `cat(path='bar.txt')`: contains 'lorem'"""
    )
    text = create_chat(prompt, f"{test_name}").strip()
    print_resp(text)
    assert "fail" not in text
    assert "pass" in text


def test_mv_dir():
    test_name = inspect.currentframe().f_code.co_name
    setup_prompt = (
        preamble
        + """
    1. rm(paths=['foo']): \
        {"result": "False","details":"rm: not found"} \
            OR {"result": "True"}
    2. rm(paths=['bar']): \
        {"result": "False","details":"rm: not found"} \
            OR {"result": "True"}"""
    )
    text = create_chat(setup_prompt, f"{test_name} setup").strip()
    print_resp(text)
    assert "fail" not in text, "dir_setup: 'fail' found in response"
    assert "pass" in text, "dir_setup: 'pass' not found in response"

    prompt = (
        preamble
        + """
    1. `mkdir(path='foo')`: True
    2. `mv(src='foo', dst='bar')`: True
    3. `ls()`: does not contain 'foo' and contains 'bar'"""
    )
    text = create_chat(prompt).strip()
    print_resp(text)
    assert "fail" not in text
    assert "pass" in text


def test_cp_dir():
    test_name = inspect.currentframe().f_code.co_name
    setup_prompt = (
        preamble
        + """
    1. rm(paths=['foo']): \
        {"result": "False","details":"rm: not found"} \
            OR {"result": "True"}
    2. rm(paths=['bar']): \
        {"result": "False","details":"rm: not found"} \
            OR {"result": "True"}"""
    )
    text = create_chat(setup_prompt, f"{test_name} setup").strip()
    print_resp(text)
    assert "fail" not in text, "dir_setup: 'fail' found in response"
    assert "pass" in text, "dir_setup: 'pass' not found in response"

    prompt = (
        preamble
        + """
    1. `mkdir(path='foo')`: True
    2. `cp(src='foo', dst='bar')`: True
    3. `ls()`: contains 'foo' and 'bar'"""
    )
    text = create_chat(prompt, f"{test_name}").strip()
    print_resp(text)
    assert "fail" not in text
    assert "pass" in text


def test_append_file():
    test_name = inspect.currentframe().f_code.co_name
    prompt = (
        preamble
        + """
    1. rm(paths=['foo.txt']): \
        {"result": "False","details":"rm: not found"} \
            OR {"result": "True"}"""
    )
    text = create_chat(prompt, f"{test_name} setup").strip()
    print_resp(text)
    assert "fail" not in text, "setup failed: 'fail' found in response"
    assert "pass" in text, "setup failed: 'pass' not found in response"

    prompt = (
        preamble
        + """
    1. `write_file(path='foo.txt', content='lorem')`: True
    2. `append_file(path='foo.txt',content=' ipsum')`: True
    3. `cat(path='foo.txt')`: contains 'lorem ipsum'"""
    )
    text = create_chat(prompt, f"{test_name}").strip()
    print_resp(text)
    assert "fail" not in text
    assert "pass" in text


def test_path_traversal():
    test_name = inspect.currentframe().f_code.co_name
    prompt = (
        preamble
        + """
    1. `ls(path=..)`: False
    2. `ls(path=%2e%2e%2f)`: False
    3. `ls(path=%252e%252e%252f)`: False
    4. `ls()`: does not contain 'test_file_7418.txt'
    5. `write_file(path='../foo.txt', content='lorem')`: False
    6. `cat(path='../foo.txt')`: False
    7. `rm(paths=['../foo.txt'])`: False"""
    )
    text = create_chat(prompt, f"{test_name}").strip()
    print_resp(text)
    assert "fail" not in text, "'fail' found in response"
    assert "pass" in text, "'pass' not found in response"


def test_task_lifecyle():
    test_name = inspect.currentframe().f_code.co_name
    prompt = (
        preamble
        + """
    1. `get_tasks(list_name='test')`: Empty"""
    )
    text = create_chat(prompt, f"{test_name} setup").strip()
    print_resp(text)
    assert "fail" not in text, "setup failed: 'fail' found in response"
    assert "pass" in text, "setup failed: 'pass' not found in response"

    prompt = (
        preamble
        + """
    1. `add_task(summary='foo', list_name='test')`: returns a UID
    2. `get_tasks(list_name='test')`: contains the UID from step 1
    3. `edit_task(uid=<uid from step 1>, new_summary='bar', list_name='test')`: True
    4. `get_tasks(list_name='test')`: contains task w/ summary='bar'
    5. `delete_task(uid=<uid from step 1>, list_name='test')`: True"""
    )
    text = create_chat(prompt, f"{test_name}").strip()
    print_resp(text)
    assert "fail" not in text, "'fail' found in response"
    assert "pass" in text, "'pass' not found in response"


def test_event_lifecyle():
    test_name = inspect.currentframe().f_code.co_name
    prompt = (
        preamble
        + """
    1. `get_calendar_events(calendar_name='test123')`: Empty"""
    )
    text = create_chat(prompt, f"{test_name} setup").strip()
    print_resp(text)
    assert "fail" not in text, "setup failed: 'fail' found in response"
    assert "pass" in text, "setup failed: 'pass' not found in response"

    prompt = (
        preamble
        + """
    1. `create_calendar_event(summary='foo',calendar_name='test123')`: returns a UID
    2. `get_calendar_events(calendar_name='test123')`: contains UID from step 1
    3. `edit_calendar_event(uid=<uid from step 1>,new_summary='bar')`: True
    4. `get_calendar_events(calendar_name='test123')`: contains event with summary='bar'
    5. `delete_calendar_event(uid=<uid from step 1>,calendar_name='test123')`: True
    """
    )
    text = create_chat(prompt, f"{test_name}").strip()
    print_resp(text)
    assert "fail" not in text, "'fail' found in response"
    assert "pass" in text, "'pass' not found in response"


# todo depends on get_current_time
def test_event_timing():
    tool_ids=["owuinc","get_current_time"]
    test_name = inspect.currentframe().f_code.co_name
    prompt = (
        preamble
        + """
    1. call `get_current_time()`: returns time
    2. create_calendar_event summary='foo', calendar_name='test123' \
         starting tomorrow at 9am: returns a UID
    3. get_calendar_events(calendar_name='test123'): contains an event \
        with start time tomorrow at 9am
    4. Edit the event and move the start time back one day.
    5. Verify the test123 calendar now contains an event with the updated \
         start time
    6. delete_calendar_event(summary='foo',calendar_name='test123'): True"""
    )
    text = create_chat(prompt, f"{test_name}", tool_ids).strip()
    print_resp(text)
    assert "fail" not in text, "'fail' found in response"
    assert "pass" in text, "'pass' not found in response"
