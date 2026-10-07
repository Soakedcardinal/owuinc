"""Thin Open WebUI REST client, just enough to install plugins and drive requests."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

from .mock_llm import MODEL


class OWUI:
    def __init__(self, url: str, email: str, password: str, timeout: float = 180):
        # lazy import: CI (and any env without the client) can still collect
        # and skip these tests instead of erroring at module import time
        import httpx

        self.http = httpx.Client(base_url=url, timeout=timeout)
        self.email, self.password = email, password

    # ---- startup ---------------------------------------------------------
    def wait_ready(self, timeout: float = 300):
        """Block until: server up, admin can sign in, and the mock model is listed."""
        import httpx

        deadline, err = time.time() + timeout, None
        while time.time() < deadline:
            try:
                r = self.http.post(
                    "/api/v1/auths/signin",
                    json={"email": self.email, "password": self.password},
                )
                if r.status_code == 200:
                    self.http.headers["Authorization"] = f"Bearer {r.json()['token']}"
                    models = self.http.get("/api/models").json().get("data", [])
                    if any(m["id"] == MODEL for m in models):
                        return
                    err = f"model {MODEL!r} not listed yet"
                else:
                    err = f"signin -> {r.status_code}"
            except httpx.TransportError as e:
                err = repr(e)
            time.sleep(2)
        raise TimeoutError(f"Open WebUI not ready: {err}")

    @staticmethod
    def _ok(r: httpx.Response) -> httpx.Response:
        if (
            r.status_code >= 400
        ):  # surface the server's message; API drift shows up here
            raise AssertionError(
                f"{r.request.method} {r.request.url.path} -> {r.status_code}: {r.text}"
            )
        return r

    # ---- installing plugins ---------------------------------------------
    def install_function(self, path: str) -> str:
        """Create-or-update a Function (filter/pipe/action) from a .py file, then
        make it active + global so it runs on every request. Returns its id."""
        fid = Path(path).stem
        payload = {
            "id": fid,
            "name": fid,
            "content": Path(path).read_text(),
            "meta": {"description": fid},
        }
        exists = self.http.get(f"/api/v1/functions/id/{fid}").status_code == 200
        self._ok(
            self.http.post(
                (
                    f"/api/v1/functions/id/{fid}/update"
                    if exists
                    else "/api/v1/functions/create"
                ),
                json=payload,
            )
        )
        self.set_function_flags(fid, active=True, glob=True)
        return fid

    def set_function_flags(self, fid: str, *, active: bool, glob: bool):
        # The API only offers toggles, so read current state and flip only if needed.
        state = self._ok(self.http.get(f"/api/v1/functions/id/{fid}")).json()
        if state["is_active"] != active:
            self._ok(self.http.post(f"/api/v1/functions/id/{fid}/toggle"))
        if state["is_global"] != glob:
            self._ok(self.http.post(f"/api/v1/functions/id/{fid}/toggle/global"))

    def install_tool(self, path: str) -> str:
        """Create-or-update a workspace Tool from a .py file. Returns its id."""
        tid = Path(path).stem
        payload = {
            "id": tid,
            "name": tid,
            "content": Path(path).read_text(),
            "meta": {"description": tid},
        }
        exists = any(
            t["id"] == tid for t in self._ok(self.http.get("/api/v1/tools/")).json()
        )
        self._ok(
            self.http.post(
                f"/api/v1/tools/id/{tid}/update" if exists else "/api/v1/tools/create",
                json=payload,
            )
        )
        return tid

    # ---- driving requests -----------------------------------------------
    def complete(self, prompt: str, **extra) -> dict:
        """Plain non-streaming /api/chat/completions. Runs inlet() filters."""
        body = {
            "model": MODEL,
            "stream": False,
            "messages": [{"role": "user", "content": prompt}],
            **extra,
        }
        return self._ok(self.http.post("/api/chat/completions", json=body)).json()

    def stream(self, prompt: str, **extra) -> list[dict]:
        """Streaming request; returns the parsed SSE chunks AS THE CLIENT SEES THEM,
        i.e. after stream() filters ran."""
        body = {
            "model": MODEL,
            "stream": True,
            "messages": [{"role": "user", "content": prompt}],
            **extra,
        }
        chunks: list[dict] = []
        with self.http.stream("POST", "/api/chat/completions", json=body) as r:
            if r.status_code >= 400:
                r.read()
                raise AssertionError(
                    f"POST /api/chat/completions -> {r.status_code}: {r.text}"
                )
            for line in r.iter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    chunks.append(json.loads(line[6:]))
        return chunks

    def stream_text(self, prompt: str, **extra) -> str:
        return "".join(
            c["choices"][0]["delta"].get("content") or ""
            for c in self.stream(prompt, **extra)
            if c.get("choices")
        )

    def outlet(self, messages: list[dict]) -> dict:
        """Run outlet() filters over a finished conversation and return the filtered
        payload. (Direct API calls don't return outlet output inline, so this is
        the supported route: POST /api/chat/completed.)"""
        body = {
            "model": MODEL,
            "messages": messages,
            "id": "test-message-id",
            "chat_id": "",
            "session_id": "",
        }
        return self._ok(self.http.post("/api/chat/completed", json=body)).json()

    def run_with_tools(self, prompt: str, tool_ids: list[str]) -> dict:
        """Run the server-side native tool loop and return the final assistant message.

        The loop only runs for a saved chat + streaming, so: create chat -> completion
        (blocks until the loop finishes, because no session_id) -> read message -> delete.
        """
        user_id, asst_id, now = str(uuid.uuid4()), str(uuid.uuid4()), int(time.time())
        chat = {
            "title": "test",
            "models": [MODEL],
            "history": {
                "currentId": asst_id,
                "messages": {
                    user_id: {
                        "id": user_id,
                        "role": "user",
                        "content": prompt,
                        "timestamp": now,
                        "models": [MODEL],
                        "childrenIds": [asst_id],
                    },
                    asst_id: {
                        "id": asst_id,
                        "role": "assistant",
                        "content": "",
                        "parentId": user_id,
                        "childrenIds": [],
                        "model": MODEL,
                        "modelName": MODEL,
                        "modelIdx": 0,
                        "done": False,
                        "timestamp": now + 1,
                    },
                },
            },
        }
        chat_id = self._ok(
            self.http.post("/api/v1/chats/new", json={"chat": chat})
        ).json()["id"]
        try:
            self._ok(
                self.http.post(
                    "/api/chat/completions",
                    json={
                        "model": MODEL,
                        "stream": True,
                        "messages": [{"role": "user", "content": prompt}],
                        "chat_id": chat_id,
                        "id": asst_id,
                        "tool_ids": tool_ids,
                        "background_tasks": {
                            "title_generation": False,
                            "tags_generation": False,
                            "follow_up_generation": False,
                        },
                    },
                )
            )
            done = self._ok(self.http.get(f"/api/v1/chats/{chat_id}")).json()
            return done["chat"]["history"]["messages"][asst_id]
        finally:
            self.http.delete(f"/api/v1/chats/{chat_id}")
