"""
title: startup_context_injector
author: Soakedcardinal
git_url: https://github.com/soakedcardinal/owuinc
description: Injects files from nextcloud as system instructions on first turn.
requirements: aiowebdav2,tiktoken
version: 1.2.0
license: MIT
"""

import os
import urllib.parse
from datetime import date, timedelta
from io import BytesIO
from typing import List, Optional

import tiktoken
from aiowebdav2 import Client as WebDAVClient
from aiowebdav2.exceptions import (
    ConnectionExceptionError,
    NoConnectionError,
    RemoteResourceNotFoundError,
    WebDavError,
)
from pydantic import BaseModel, Field

_tokenizer = tiktoken.get_encoding("cl100k_base")


def _token_count(text: str) -> int:
    """Count tokens using tiktoken's cl100k_base encoder."""
    return len(_tokenizer.encode(text))


def validate_path(path, valves):
    """
    Validate and normalize file paths for WebDAV operations.

    SECURITY MODEL:
    - All operations are confined to SANDBOX_DIR (e.g., "owuinc/")
    - Path traversal ("..") is explicitly blocked
    - Absolute paths ("/etc/passwd") are stripped and treated as relative
      to sandbox root ("/etc/passwd" -> "owuinc/etc/passwd")

    NOTE: SANDBOX_DIR is auto-created on first use if it doesn't exist.

    Args:
        path: User-provided path (can be relative, absolute, or empty)
        valves: Configuration object with SANDBOX_DIR setting

    Returns:
        Full WebDAV path prefixed with sandbox directory
        (e.g., "owuinc/Documents/file.py")

    Raises:
        Exception: If path contains traversal attempts ("..")

    Examples:
        validate_path("", valves)         # -> "owuinc/"
        validate_path(".", valves)        # -> "owuinc/"
        validate_path("/", valves)        # -> "owuinc/"
        validate_path("Documents/", valves)  # -> "owuinc/Documents/"
        validate_path("/etc", valves)     # -> "owuinc/etc" (strips leading /)
        validate_path("../etc", valves)   # -> Exception (traversal blocked)
    """
    prefix = valves.SANDBOX_DIR.strip().rstrip("/") + "/"

    if not path:
        return prefix

    path = path.strip()

    prev = None
    while prev != path:
        prev = path
        path = urllib.parse.unquote(path)

    if ".." in path:
        raise Exception("Invalid Path: traversal not allowed")

    if path in ("", ".", "/"):
        return prefix

    if path.startswith("/"):
        path = path.lstrip("/")

    full_path = prefix + os.path.normpath(path)
    if full_path.startswith(prefix):
        return full_path

    raise Exception("Invalid Path: outside sandbox.")


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
        info = {
            "name": filename,
            "tokens": _token_count(content),
        }
        injected_info.append(info)
        return info
    return None


def _webdav_path(p: str) -> str:
    """Ensure path has leading / for aiowebdav2."""
    return p if p.startswith("/") else "/" + p


class Filter:
    """
    OWUI Filter that auto-injects system files as context before every LLM request.
    Prepends all as system messages in the chat body.
    """

    class Valves(BaseModel):
        NEXTCLOUD_BASE_URL: str = Field("", description="Nextcloud server address")
        WEBDAV_USERNAME: str = Field("")
        NEXTCLOUD_USERNAME: str = Field("")
        NEXTCLOUD_APP_PASSWORD: str = Field("", json_schema_extra={"secret": True})
        SANDBOX_DIR: str = Field(
            default="",
            description=(
                "Directory containing system files on Nextcloud. "
                "Leave empty to use root. Leading / will be stripped."
            ),
        )
        FILES_TO_INJECT: str = Field(
            default="AGENTS.md,SOUL.md,IDENTITY.md,TOOLS.md,STYLE.md,USER.md,MEMORY.md",
            description="Comma-separated list of files to inject (in order)",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def _download_file(self, client: WebDAVClient, path: str) -> Optional[str]:
        """Download a single file from WebDAV."""
        try:
            buf = BytesIO()
            await client.resource(_webdav_path(path)).read_from(buf)
            return buf.getvalue().decode("utf-8")
        except RemoteResourceNotFoundError:
            return None
        except WebDavError:
            return None
        except Exception:
            return None

    def _get_today_filename(self) -> str:
        """Return today's date as YYYY-MM-DD.md"""
        return date.today().strftime("%Y-%m-%d") + ".md"

    def _get_yesterday_filename(self) -> str:
        """Return yesterday's date as YYYY-MM-DD.md"""
        yesterday = date.today() - timedelta(days=1)
        return yesterday.strftime("%Y-%m-%d") + ".md"

    async def _build_context(self, __event_emitter__=None) -> list[str]:
        """Download all system files and return list of context strings."""
        base = self.valves.NEXTCLOUD_BASE_URL.rstrip("/")
        wd_user = self.valves.WEBDAV_USERNAME.strip()
        nc_url = f"{base}/remote.php/dav/files/{wd_user}/"

        client = WebDAVClient(
            nc_url,
            self.valves.NEXTCLOUD_USERNAME,
            self.valves.NEXTCLOUD_APP_PASSWORD,
        )

        try:
            files_to_inject = [
                f.strip() for f in self.valves.FILES_TO_INJECT.split(",") if f.strip()
            ]

            contexts: List[str] = []
            injected_info: list[dict] = []

            for filename in files_to_inject:
                try:
                    validated = validate_path(filename, self.valves)
                except Exception:
                    continue
                path = validated.rstrip("/")
                content = await self._download_file(client, path)
                info = _try_inject(contexts, injected_info, filename, content)
                if info and __event_emitter__:
                    desc = f"{info['name']} ({info['tokens']} tokens)"
                    await __event_emitter__(
                        {
                            "type": "status",
                            "data": {
                                "description": desc,
                                "done": False,
                            },
                        }
                    )

            memory_base = validate_path("memory", self.valves).rstrip("/")

            today_file = self._get_today_filename()
            yesterday_file = self._get_yesterday_filename()

            for log_file in [yesterday_file, today_file]:
                path = f"{memory_base}/{log_file}"
                content = await self._download_file(client, path)
                info = _try_inject(
                    contexts, injected_info, f"memory/{log_file}", content
                )
                if info and __event_emitter__:
                    desc = f"{info['name']} ({info['tokens']} tokens)"
                    await __event_emitter__(
                        {
                            "type": "status",
                            "data": {
                                "description": desc,
                                "done": False,
                            },
                        }
                    )

            # Emit final summary.
            if injected_info and __event_emitter__:
                total_tokens = sum(f["tokens"] for f in injected_info)
                n_files = len(injected_info)
                desc = f"Context injected: {total_tokens} tokens ({n_files} files)"
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {
                            "description": desc,
                            "done": True,
                        },
                    }
                )

            return contexts
        finally:
            await client.close()

    async def inlet(self, body: dict, __event_emitter__=None) -> dict:
        """
        Inlet filter that injects system files as context before LLM request.

        Only runs on FIRST TURN of a new chat (when there's 1 user message total).
        Subsequent turns in an ongoing conversation skip injection to avoid redundancy.

        Args:
            body: Chat completion request body with messages array
            __event_emitter__: Optional event emitter for UI status feedback

        Returns:
            Modified body with system context prepended (first turn only)
        """
        try:
            # Check if this is the first turn of a new chat
            messages = body.get("messages", [])

            # Count user messages (assistant responses indicate ongoing conversation)
            user_messages = [m for m in messages if m.get("role") == "user"]
            has_assistant_response = any(m.get("role") == "assistant" for m in messages)

            if has_assistant_response or len(user_messages) != 1:
                return body

            # This is the first turn - download and inject context
            contexts = await self._build_context(__event_emitter__)

            if not contexts:
                return body

            # Combine all contexts into single message
            full_context = "\n\n".join(contexts)

            # Create system message at position 0 (before user messages)
            context_message = {"role": "system", "content": full_context}

            body.setdefault("messages", []).insert(0, context_message)

            return body

        except (ConnectionExceptionError, NoConnectionError):
            pass
        except Exception:
            pass
        return body
