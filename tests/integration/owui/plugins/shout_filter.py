"""
title: Shout Filter
description: Example filter exercising inlet, stream and outlet.
"""

from pydantic import BaseModel


class Filter:
    class Valves(BaseModel):
        system_prompt: str = "Answer in capital letters."

    def __init__(self):
        self.valves = self.Valves()

    async def inlet(self, body: dict, __user__: dict | None = None) -> dict:
        body["messages"].insert(
            0, {"role": "system", "content": self.valves.system_prompt}
        )
        return body

    async def stream(self, event: dict) -> dict:
        for choice in event.get("choices", []):
            delta = choice.get("delta", {})
            if delta.get("content"):
                delta["content"] = delta["content"].upper()
        return event

    async def outlet(self, body: dict, __user__: dict | None = None) -> dict:
        for m in body["messages"]:
            if m["role"] == "assistant":
                m["content"] += "\n[checked]"
        return body
