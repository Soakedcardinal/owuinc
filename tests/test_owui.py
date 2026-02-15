import json
import os
import re
import time

import dotenv
import requests

dotenv.load_dotenv(dotenv.find_dotenv())
url = os.getenv("URL")
key = os.getenv("KEY")
user_id = os.getenv("USER_ID")
folder_id = os.getenv("FOLDER_ID")

if not url or not key or not user_id or not folder_id:
    raise ValueError("missing required environment variable")

header = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
header_2 = {"Authorization": f"Bearer {key}"}

model = "origin"
tool_ids = ["nextcloud"]

preamble = """Run the test immediately by invoking each tool and verifying the results.

Stop immediately if errors occur.

**STRICT OUTPUT CONSTRAINTS:**
*   Output **ONLY** the strings "pass" or "fail".
*   The response must be mutually exclusive to any other text.
*   No preamble, no postscript, no partial sentences."""


def create_chat(model, tools, prompt) -> str:
    ts = int(time.time())

    # create chat
    payload = {
        "chat": {
            "title": "create chat",
            "user_id": user_id,
            "models": [model],
            "messages": [
                {
                    "id": "user-msg-id",
                    "role": "user",
                    "content": prompt,
                    "timestamp": ts,
                    "models": [model],
                },
                {
                    "id": "assistant-msg-id",
                    "role": "assistant",
                    "content": "",
                    "parentId": "user-msg-id",
                    "modelName": model,
                    "modelIdx": 0,
                    "timestamp": ts + 1000,
                },
            ],
            "history": {
                "current_id": "user-msg-id",
                "messages": {
                    "user-msg-id": {
                        "id": "user-msg-id",
                        "role": "user",
                        "content": prompt,
                        "timestamp": ts,
                        "models": [model],
                    }
                },
                "assistant-msg-id": {
                    "id": "assistant-msg-id",
                    "role": "assistant",
                    "content": "",
                    "parentId": "user-msg-id",
                    "modelName": model,
                    "modelIdx": 0,
                    "timestamp": ts + 1000,
                },
            },
        },
        "folder_id": folder_id,
    }
    resp = requests.post(
        f"{url}/v1/chats/new",
        headers=header,
        data=json.dumps(payload),
    )
    id = resp.json()["id"]
    chat_endpoint = f"{url}/v1/chats/{id}"

    # trigger completion
    completion_payload = {
        "chat_id": id,
        "id": "assistant-msg-id",
        "messages": [{"role": "user", "content": prompt}],
        "model": model,
        "stream": "true",
        "background_tasks": {
            "title_generation": "false",
            "tags_generation": "false",
            "follow_up_generation": "false",
        },
        "features": {
            "code_interpreter": "false",
            "web_search": "false",
            "image_generation": "false",
            "memory": "false",
        },
        "session_id": "session-id",
        "tool_ids": tools,
    }
    resp = requests.post(
        f"{url}/chat/completions",
        headers=header,
        data=json.dumps(completion_payload),
    )
    # wait for assistant completion
    while True:
        resp = requests.get(chat_endpoint, headers=header_2)
        # Find the assistant message
        msg = (
            resp.json()
            .get("chat", {})
            .get("history", {})
            .get("messages", {})
            .get("assistant-msg-id", {})
            .get("content", "")
        )
        if msg:  # got msg
            break
        time.sleep(1)

    # Complete Assistant Message
    payload = {
        "chat_id": id,
        "id": "assistant-msg-id",
        "session_id": "session-id",
        "model": model,
    }
    resp = requests.get(
        f"{url}/chat/completed",
        headers=header,
        data=json.dumps(payload),
    )

    # get final chat
    resp = requests.get(chat_endpoint, headers=header_2)

    # extract msg
    msg = (
        resp.json()
        .get("chat", {})
        .get("history", {})
        .get("messages", {})
        .get("assistant-msg-id", {})
        .get("content", "")
    )
    msg = re.sub(r"<details[^>]*>.*?</details>", "", msg, flags=re.DOTALL)
    return msg


def print_sep():
    print("----------------------------")


def print_resp(text):
    print_sep()
    print(text)


def test_mv_file():
    prompt = (
        preamble
        + """
    1. rm(paths=['foo.txt']): \
        {"result": "False","details":"rm: not found"} \
            OR {"result": "True"}
    2. rm(paths=['bar.txt']): \
        {"result": "False","details":"rm: not found"} \
            OR {"result": "True"}"""
    )
    text = create_chat(model, tool_ids, prompt).strip()
    print_resp(text)
    assert "fail" not in text
    assert "pass" in text

    prompt = (
        preamble
        + """
    1. `write_file(path='foo.txt', content='lorem')`: True
    2. `mv(src='foo.txt', dst='bar.txt')`: True
    3. `cat(path='foo.txt')`: False
    4. `cat(path='bar.txt')`: contains 'lorem'"""
    )
    text = create_chat(model, tool_ids, prompt).strip()
    print_resp(text)
    assert "fail" not in text
    assert "pass" in text


def test_cp_file():
    prompt = (
        preamble
        + """
    1. rm(paths=['foo.txt']): \
        {"result": "False","details":"rm: not found"} \
            OR {"result": "True"}
    2. rm(paths=['bar.txt']): \
        {"result": "False","details":"rm: not found"} \
            OR {"result": "True"}"""
    )
    text = create_chat(model, tool_ids, prompt).strip()
    print_resp(text)
    assert "fail" not in text
    assert "pass" in text

    prompt = (
        preamble
        + """
    1. `write_file(path='foo.txt', content='lorem')`: True
    2. `cp(src='foo.txt', dst='bar.txt)`: True
    3. `cat(path='foo.txt')`: contains 'lorem'
    4. `cat(path='bar.txt')`: contains 'lorem'"""
    )
    text = create_chat(model, tool_ids, prompt).strip()
    print_resp(text)
    assert "fail" not in text
    assert "pass" in text


def test_mv_dir():
    prompt = (
        preamble
        + """
    1. rm(paths=['foo']): \
        {"result": "False","details":"rm: not found"} \
            OR {"result": "True"}
    2. rm(paths=['bar']): \
        {"result": "False","details":"rm: not found"} \
            OR {"result": "True"}"""
    )
    text = create_chat(model, tool_ids, prompt).strip()
    print_resp(text)
    assert "fail" not in text
    assert "pass" in text

    prompt = (
        preamble
        + """
    1. `mkdir(path='foo')`: True
    2. `mv(src='foo', dst='bar')`: True
    3. `ls()`: does not contain 'foo' and contains 'bar'"""
    )
    text = create_chat(model, tool_ids, prompt).strip()
    print_resp(text)
    assert "fail" not in text
    assert "pass" in text


def test_cp_dir():
    prompt = (
        preamble
        + """
    1. rm(paths=['foo']): \
        {"result": "False","details":"rm: not found"} \
            OR {"result": "True"}
    2. rm(paths=['bar']): \
        {"result": "False","details":"rm: not found"} \
            OR {"result": "True"}"""
    )
    text = create_chat(model, tool_ids, prompt).strip()
    print_resp(text)
    assert "fail" not in text
    assert "pass" in text

    prompt = (
        preamble
        + """
    1. `mkdir(path='foo')`: True
    2. `cp(src='foo', dst='bar')`: True
    3. `ls()`: contains 'foo' and 'bar'"""
    )
    text = create_chat(model, tool_ids, prompt).strip()
    print_resp(text)
    assert "fail" not in text
    assert "pass" in text


def test_append_file():
    prompt = (
        preamble
        + """
    1. rm(paths=['foo.txt']): \
        {"result": "False","details":"rm: not found"} \
            OR {"result": "True"}"""
    )
    text = create_chat(model, tool_ids, prompt).strip()
    print_resp(text)
    assert "fail" not in text
    assert "pass" in text

    prompt = (
        preamble
        + """
    1. `write_file(path='foo.txt', content='lorem')`: True
    2. `append_file(path='foo.txt',content=' ipsum')`: True
    3. `cat(path='foo.txt')`: contains 'lorem ipsum'"""
    )
    text = create_chat(model, tool_ids, prompt).strip()
    print_resp(text)
    assert "fail" not in text
    assert "pass" in text


def test_path_traversal():
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
    text = create_chat(model, tool_ids, prompt).strip()
    print_resp(text)
    assert "fail" not in text
    assert "pass" in text


def test_task_lifecyle():
    prompt = (
        preamble
        + """
    1. `get_tasks(list_name='test')`: Empty"""
    )
    text = create_chat(model, tool_ids, prompt).strip()
    print_resp(text)
    assert "fail" not in text
    assert "pass" in text

    prompt = (
        preamble
        + """
    1. `add_task(summary='foo', list_name='test')`: returns a UID
    2. `get_tasks(list_name='test')`: contains the UID from step 1
    3. `edit_task(uid=<uid from step 1>, new_summary='bar', list_name='test')`: True
    4. `get_tasks(list_name='test')`: contains task w/ summary='bar'
    5. `delete_task(uid=<uid from step 1>, list_name='test')`: True"""
    )
    text = create_chat(model, tool_ids, prompt).strip()
    print_resp(text)
    assert "fail" not in text
    assert "pass" in text


def test_event_lifecyle():
    prompt = (
        preamble
        + """
    1. `get_calendar_events(calendar_name='test123')`: Empty"""
    )
    text = create_chat(model, tool_ids, prompt).strip()
    print_resp(text)
    assert "fail" not in text
    assert "pass" in text

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
    text = create_chat(model, tool_ids, prompt).strip()
    print_resp(text)
    assert "fail" not in text
    assert "pass" in text


# todo depends on get_current_time
def test_event_timing():
    prompt = (
        preamble
        + """
    1. get the current time
    2. create_calendar_event summary='foo', calendar_name='test123' \
         starting tomorrow at 9am: returns a UID
    3. get_calendar_events(calendar_name='test123'): contains an event \
        with start time tomorrow at 9am
    4. Edit the event and move the start time back one day.
    5. Verify the test123 calendar now contains an event with the updated \
         start time
    6. delete_calendar_event(summary='foo',calendar_name='test123'): True"""
    )
    text = create_chat(model, tool_ids, prompt).strip()
    print_resp(text)
    assert "fail" not in text
    assert "pass" in text
