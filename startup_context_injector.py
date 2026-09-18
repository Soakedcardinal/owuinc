"""
title: startup_context_injector
author: Soakedcardinal
git_url: https://github.com/soakedcardinal/owuinc
description: Injects files from nextcloud as system instructions on every request.
requirements: aiowebdav2,tiktoken
version: 1.6.0
license: MIT
"""

import asyncio
import os
import re
import urllib.parse
from datetime import date, datetime, timedelta
from io import BytesIO
from typing import List, Optional

import tiktoken
from aiohttp import ClientTimeout
from aiowebdav2 import Client as WebDAVClient
from aiowebdav2.client import ClientOptions
from aiowebdav2.exceptions import (
    RemoteResourceNotFoundError,
    WebDavError,
)
from pydantic import BaseModel, Field

_tokenizer = tiktoken.get_encoding("cl100k_base")


def _token_count(text: str) -> int:
    """Count tokens using tiktoken's cl100k_base encoder."""
    return len(_tokenizer.encode(text))


_CTX_BEGIN = "<!-- owuinc:context:begin -->"
_CTX_END = "<!-- owuinc:context:end -->"
_CTX_BLOCK_RE = re.compile(
    re.escape(_CTX_BEGIN) + r".*?" + re.escape(_CTX_END), re.DOTALL
)


def _try_inject(
    contexts: list[str],
    injected_info: list[dict],
    filename: str,
    content: Optional[str],
):
    """Wrap downloaded content in XML tags and append to contexts.

    Returns a dict with name/tokens if injected, None otherwise.
    """
    if content:
        contexts.append(f"<{filename}>\n{content}\n</{filename}>")
        info = {"name": filename, "tokens": _token_count(content)}
        injected_info.append(info)
        return info
    return None


def _webdav_path(p: str) -> str:
    """Ensure path has leading / for aiowebdav2."""
    return p if p.startswith("/") else "/" + p


def validate_path(path, valves):
    """Validate and normalize file paths for WebDAV operations.

    SECURITY MODEL:
    - All operations are confined to SANDBOX_DIR (e.g., "owuinc/")
    - Path traversal ("..") is explicitly blocked
    - Absolute paths ("/etc/passwd") are stripped and treated as relative
      to sandbox root ("/etc/passwd" -> "owuinc/etc/passwd")

    NOTE: Read-only here — paths are used only to download files from WebDAV,
    so SANDBOX_DIR is never created.

    Args:
        path: User-provided path (can be relative, absolute, or empty)
        valves: Configuration object with SANDBOX_DIR setting

    Returns:
        Full WebDAV path prefixed with sandbox directory
        (e.g., "owuinc/Documents/file.py")

    Raises:
        Exception: If path contains traversal attempts ("..")

    Examples:
        validate_path("", valves)           # -> "owuinc/"
        validate_path(".", valves)          # -> "owuinc/"
        validate_path("/", valves)          # -> "owuinc/"
        validate_path("Documents/", valves) # -> "owuinc/Documents/"
        validate_path("/etc", valves)       # -> "owuinc/etc" (strips leading /)
        validate_path("../etc", valves)     # -> Exception (traversal blocked)
    """
    prefix = valves.SANDBOX_DIR.strip().rstrip("/") + "/"

    if not path:
        return prefix

    path = path.strip()

    # Iteratively decode URL encoding to catch multi-layer attacks.
    prev = None
    while prev != path:
        prev = path
        path = urllib.parse.unquote(path)

    if ".." in path:
        raise Exception("Invalid Path: traversal not allowed")

    if any(ord(c) < 32 for c in path):
        raise Exception("Invalid Path: control characters not allowed")

    if path in ("", ".", "/"):
        return prefix

    if path.startswith("/"):
        path = path.lstrip("/")

    full_path = prefix + os.path.normpath(path)
    if full_path.startswith(prefix):
        return full_path

    raise Exception("Invalid Path: outside sandbox.")


class Filter:
    """OWUI Filter that auto-injects system files as context before every LLM request.

    Prepends all as system messages in the chat body.
    """

    valves: "Valves"

    class Valves(BaseModel):
        NEXTCLOUD_BASE_URL: str = Field("", description="Nextcloud server address")
        WEBDAV_USERNAME: str = Field("")
        NEXTCLOUD_USERNAME: str = Field("")
        NEXTCLOUD_APP_PASSWORD: str = Field(
            "",
            json_schema_extra={"secret": True, "input": {"type": "password"}},
        )
        SANDBOX_DIR: str = Field(
            default="owuinc",
            description=(
                "Directory containing system files on Nextcloud. Leading / will be stripped. Must match owuinc tool's SANDBOX_DIR."
            ),
        )
        FILES_TO_INJECT: str = Field(
            default="AGENTS.md,SOUL.md,IDENTITY.md,TOOLS.md,STYLE.md,USER.md,MEMORY.md",
            description=(
                "Comma-separated list of files to inject (in order). Missing files are skipped. Daily logs are inserted after MEMORY.md per the INJECT_* switches below (appended if MEMORY.md is absent)."
            ),
        )
        INJECT_TODAY: bool = Field(
            True,
            description="Inject today's daily log (memory/<today>.md).",
        )
        INJECT_YESTERDAY: bool = Field(
            False,
            description="Inject yesterday's daily log (memory/<yesterday>.md).",
        )
        INJECT_2_DAYS_AGO: bool = Field(
            False,
            description="Inject the daily log from 2 days ago.",
        )
        INJECT_3_DAYS_AGO: bool = Field(
            False,
            description="Inject the daily log from 3 days ago.",
        )
        INJECT_TIME: bool = Field(
            True,
            description=(
                "Inject the session start time (full timestamp) as the first system "
                "context line, so the model knows the date and time of session start "
                "without calling get_current_time. Stale within long sessions; the "
                "tool remains available for live time."
            ),
        )
        INJECT_POSITION: str = Field(
            default="after",
            json_schema_extra={"enum": ["before", "after"]},
            description=(
                "Place the injected context after (default) or before the model's own system prompt. The existing system prompt is never discarded either way."
            ),
        )
        REQUEST_TIMEOUT: int = Field(
            default=10,
            ge=1,
            le=120,
            description="WebDAV request timeout in seconds (1-120)",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def _download_file(self, client: WebDAVClient, path: str) -> Optional[str]:
        try:
            buf = BytesIO()
            await client.resource(_webdav_path(path)).read_from(buf)
            return buf.getvalue().decode("utf-8")
        except (RemoteResourceNotFoundError, WebDavError, UnicodeDecodeError):
            return None

    def _get_log_filename(self, days_ago: int) -> str:
        return (date.today() - timedelta(days=days_ago)).strftime("%Y-%m-%d") + ".md"

    async def _emit_status(self, emitter, description: str, done: bool):
        """Emit a UI status event."""
        if emitter:
            await emitter(
                {
                    "type": "status",
                    "data": {
                        "description": description,
                        "done": done,
                    },
                }
            )

    async def _build_context(self) -> tuple[list[str], list[dict]]:
        base = self.valves.NEXTCLOUD_BASE_URL.rstrip("/")
        wd_user = self.valves.WEBDAV_USERNAME.strip()
        nc_url = f"{base}/remote.php/dav/files/{wd_user}/"

        client = WebDAVClient(
            nc_url,
            self.valves.NEXTCLOUD_USERNAME,
            self.valves.NEXTCLOUD_APP_PASSWORD,
            options=ClientOptions(
                timeout=ClientTimeout(total=self.valves.REQUEST_TIMEOUT)
            ),
        )

        try:
            files_to_inject = [
                f.strip() for f in self.valves.FILES_TO_INJECT.split(",") if f.strip()
            ]

            # Build path map.
            file_paths: list[tuple[str, str]] = []
            for filename in files_to_inject:
                try:
                    validated = validate_path(filename, self.valves)
                    file_paths.append((filename, validated.rstrip("/")))
                except Exception:
                    continue
            memory_base = validate_path("memory", self.valves).rstrip("/")
            daily_paths: list[tuple[str, str]] = []
            for days_ago, enabled in [
                (0, self.valves.INJECT_TODAY),
                (1, self.valves.INJECT_YESTERDAY),
                (2, self.valves.INJECT_2_DAYS_AGO),
                (3, self.valves.INJECT_3_DAYS_AGO),
            ]:
                if not enabled:
                    continue
                log_file = self._get_log_filename(days_ago)
                daily_paths.append((f"memory/{log_file}", f"{memory_base}/{log_file}"))

            insert_at = next(
                (
                    i + 1
                    for i, (f, _) in enumerate(file_paths)
                    if f.rsplit("/", 1)[-1].lower() == "memory.md"
                ),
                len(file_paths),
            )
            file_paths[insert_at:insert_at] = daily_paths

            # Download all content.
            content_map = dict(
                zip(
                    (filename for filename, _ in file_paths),
                    await asyncio.gather(
                        *(self._download_file(client, wpath) for _, wpath in file_paths)
                    ),
                )
            )

            contexts: List[str] = []
            injected_info: list[dict] = []

            if self.valves.INJECT_TIME:
                session_start = (
                    datetime.now().astimezone().isoformat(timespec="minutes")
                )
                time_ctx = "<session_start>\n" f"{session_start}\n" "</session_start>"
                time_tokens = _token_count(time_ctx)
                contexts.append(time_ctx)
                injected_info.append({"name": "session_start", "tokens": time_tokens})

            for filename, _wpath in file_paths:
                content = content_map.get(filename)
                _try_inject(contexts, injected_info, filename, content)

            return contexts, injected_info
        finally:
            await client.close()

    def _merge_system(self, existing: str, injected: str) -> str:
        """Merge injected context into an existing system prompt.

        Never discards the operator's system prompt, and is idempotent: a block
        left by a previous call is replaced rather than stacked, since `request`
        runs once per provider call (several times in a tool-calling turn).
        """
        block = f"{_CTX_BEGIN}\n{injected}\n{_CTX_END}"
        existing = _CTX_BLOCK_RE.sub("", existing or "").strip()
        if not existing:
            return block
        if self.valves.INJECT_POSITION == "before":
            return f"{block}\n\n{existing}"
        return f"{existing}\n\n{block}"

    async def request(self, body: dict, __event_emitter__=None) -> dict:
        # Background jobs (title, tag, autocomplete generation) go through this
        # hook too and don't need the memory context — skip the fetch entirely.
        if (body.get("metadata") or {}).get("task"):
            return body

        try:
            contexts, injected_info = await self._build_context()
            if not contexts:
                return body

            content = "\n\n".join(contexts)

            for info in injected_info:
                await self._emit_status(
                    __event_emitter__,
                    f"{info['name']} ({info['tokens']} tokens)",
                    done=False,
                )
            total = sum(f["tokens"] for f in injected_info)
            await self._emit_status(
                __event_emitter__,
                f"Context injected: {total} tokens ({len(injected_info)} files)",
                True,
            )

            messages = body.setdefault("messages", [])
            if messages and messages[0].get("role") == "system":
                messages[0]["content"] = self._merge_system(
                    messages[0].get("content") or "", content
                )
            else:
                messages.insert(
                    0, {"role": "system", "content": self._merge_system("", content)}
                )

            return body

        except Exception:
            await self._emit_status(
                __event_emitter__,
                "Context injection failed: error",
                done=True,
            )
        return body
