"""A small fake OpenAI-compatible upstream. Stdlib only.

Open WebUI is pointed at this as its only model provider, so:
  * every request it sends AFTER filters ran is recorded in `.requests`
    (that's how you assert on what a filter's inlet did), and
  * replies are scripted (that's how you make the "model" call a tool).
"""

import json
import re
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MODEL = "mock-model"


@dataclass
class Reply:
    text: str = "mock reply"
    # [("tool_name", {"arg": "value"}), ...]
    tool_calls: list = field(default_factory=list)


class MockLLM:
    def __init__(self, port: int = 9999):
        self.port = port
        self.requests: list[dict] = []
        self._script: list[Reply] = []
        self._server: ThreadingHTTPServer | None = None

    # ---- test-facing API -------------------------------------------------
    def script(self, *replies: Reply):
        """Queue replies; one is consumed per upstream request."""
        self._script.extend(replies)

    def reset(self):
        self.requests.clear()
        self._script.clear()

    @property
    def last(self) -> dict:
        return self.requests[-1]

    # ---- server ----------------------------------------------------------
    def start(self):
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence
                pass

            def _json(self, obj):
                data = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # GET /v1/models
                self._json(
                    {"object": "list", "data": [{"id": MODEL, "object": "model"}]}
                )

            def do_POST(self):  # POST /v1/chat/completions
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                mock.requests.append(body)
                reply = mock._script.pop(0) if mock._script else Reply()
                if body.get("stream"):
                    self._stream(reply)
                else:
                    self._json(mock._completion(reply))

            def _stream(self, reply: Reply):
                # HTTP/1.0 handler: connection close ends the body, no chunking needed.
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for chunk in mock._chunks(reply):
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")

        self._server = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self):
        assert self._server is not None
        self._server.shutdown()

    # ---- OpenAI wire formats --------------------------------------------
    @staticmethod
    def _calls(reply: Reply, with_index: bool):
        calls: list[dict] = []
        for i, (name, args) in enumerate(reply.tool_calls):
            call: dict[str, Any] = {
                "id": f"call_{i}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
            if with_index:
                call["index"] = i
            calls.append(call)
        return calls

    def _completion(self, reply: Reply) -> dict:
        message = {
            "role": "assistant",
            "content": None if reply.tool_calls else reply.text,
        }
        if reply.tool_calls:
            message["tool_calls"] = self._calls(reply, with_index=False)
        return {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "model": MODEL,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls" if reply.tool_calls else "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    def _chunks(self, reply: Reply):
        def chunk(delta, finish=None):
            return {
                "id": "chatcmpl-mock",
                "object": "chat.completion.chunk",
                "model": MODEL,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }

        if reply.tool_calls:
            yield chunk(
                {"role": "assistant", "tool_calls": self._calls(reply, with_index=True)}
            )
            yield chunk({}, "tool_calls")
        else:
            yield chunk({"role": "assistant", "content": ""})
            # several small chunks so stream() filters see more than one event
            for piece in re.findall(r"\S+\s*", reply.text):
                yield chunk({"content": piece})
            yield chunk({}, "stop")


if __name__ == "__main__":
    mock = MockLLM()
    mock.start()
    print(f"mock llm on :{mock.port}", flush=True)
    import signal

    signal.pause()
