"""
title: Reverse Tool
description: Example tool.
"""

from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        prefix: str = Field("", description="Prepended to every result")

    def __init__(self):
        self.valves = self.Valves()

    async def reverse_string(self, string: str) -> str:
        """
        Reverses the input string.
        :param string: The string to reverse
        """
        return self.valves.prefix + string[::-1]
