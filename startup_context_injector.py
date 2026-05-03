"""
title: startup_context_injector
author: Soakedcardinal
git_url: https://github.com/soakedcardinal/owuinc
description: Injects files from nextcloud as system instructions on first turn.
requirements: aiowebdav2
version: 1.0.0
license: MIT
"""

import traceback
from datetime import date, timedelta
from io import BytesIO
from typing import List, Optional

from aiowebdav2 import Client as WebDAVClient
from aiowebdav2.exceptions import (
    ConnectionExceptionError,
    NoConnectionError,
    RemoteResourceNotFoundError,
    WebDavError,
)
from pydantic import BaseModel, Field

# DEBUG = True
DEBUG = False


def log(msg):
    if DEBUG:
        print(msg)


def log_err(msg):
    if DEBUG:
        print("ERROR: " + msg)


def log_sep(msg):
    if DEBUG:
        print("\n" + "=" * 60)
        print(f"  {msg}")
        print("=" * 60 + "\n")


def _try_inject(contexts: list[str], filename: str, content: Optional[str]):
    """Wrap downloaded content in XML tags and append to contexts."""
    if content:
        contexts.append(f"<{filename}>\n{content}\n</{filename}>")
        log(f"OK - {filename} ({len(content)} chars)")
    else:
        log(f"FAILED - {filename} not found")


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
        log_sep("startup_context_injector")
        self.valves = self.Valves()

    async def _download_file(self, client: WebDAVClient, path: str) -> Optional[str]:
        """Download a single file from WebDAV."""
        try:
            buf = BytesIO()
            await client.resource(_webdav_path(path)).read_from(buf)
            return buf.getvalue().decode("utf-8")
        except RemoteResourceNotFoundError:
            log_err(f"file not found - {path}")
            return None
        except WebDavError as e:
            log_err(f"WebDAV error for {path} - {e}")
            return None
        except Exception as e:
            log_err(
                f"unexpected error downloading {path} - "
                f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            )
            return None

    def _get_today_filename(self) -> str:
        """Return today's date as YYYY-MM-DD.md"""
        return date.today().strftime("%Y-%m-%d") + ".md"

    def _get_yesterday_filename(self) -> str:
        """Return yesterday's date as YYYY-MM-DD.md"""
        yesterday = date.today() - timedelta(days=1)
        return yesterday.strftime("%Y-%m-%d") + ".md"

    async def _build_context(self) -> list[str]:
        """Download all system files and return list of context strings."""
        base = self.valves.NEXTCLOUD_BASE_URL.rstrip("/")
        wd_user = self.valves.WEBDAV_USERNAME.strip()
        nc_url = f"{base}/remote.php/dav/files/{wd_user}/"

        log(f"creating WebDAV client for {nc_url}")

        client = WebDAVClient(
            nc_url,
            self.valves.NEXTCLOUD_USERNAME,
            self.valves.NEXTCLOUD_APP_PASSWORD,
        )

        try:
            # Parse file list from valves
            files_to_inject = [
                f.strip() for f in self.valves.FILES_TO_INJECT.split(",") if f.strip()
            ]
            log(
                f"attempting to download {len(files_to_inject)} "
                f"files: {files_to_inject}"
            )

            # Build full paths (strip leading / from SANDBOX_DIR)
            system_dir = self.valves.SANDBOX_DIR.strip().lstrip("/")
            if system_dir:
                system_dir += "/"

            contexts: List[str] = []
            for filename in files_to_inject:
                path = f"{system_dir}{filename}"
                log(f"downloading {path}...")
                content = await self._download_file(client, path)
                _try_inject(contexts, filename, content)

            # Inject daily memory logs
            log("downloading memory/*.md files...")
            memory_dir = f"{system_dir}memory/"

            today_file = self._get_today_filename()
            yesterday_file = self._get_yesterday_filename()

            for log_file in [yesterday_file, today_file]:
                path = f"{memory_dir}{log_file}"
                log(f"downloading {path}...")
                content = await self._download_file(client, path)
                _try_inject(contexts, f"memory/{log_file}", content)

            return contexts
        finally:
            await client.close()

    async def inlet(self, body: dict) -> dict:
        """
        Inlet filter that injects system files as context before LLM request.

        Only runs on FIRST TURN of a new chat (when there's 1 user message total).
        Subsequent turns in an ongoing conversation skip injection to avoid redundancy.

        Args:
            body: Chat completion request body with messages array

        Returns:
            Modified body with system context prepended (first turn only)
        """
        log_sep("inlet")

        try:
            # Check if this is the first turn of a new chat
            messages = body.get("messages", [])
            log(f"INLET - messages={len(messages)}, body_keys={list(body.keys())}")

            # Count user messages (assistant responses indicate ongoing conversation)
            user_messages = [m for m in messages if m.get("role") == "user"]
            has_assistant_response = any(m.get("role") == "assistant" for m in messages)

            log(
                f"user_msgs={len(user_messages)}, "
                f"has_assistant={has_assistant_response}"
            )

            if has_assistant_response or len(user_messages) != 1:
                log("not first turn, skipping injection")
                return body

            # This is the first turn - download and inject context
            log("FIRST TURN DETECTED - downloading system files...")

            contexts = await self._build_context()

            if not contexts:
                log("no context files downloaded, skipping injection")
                return body

            # Combine all contexts into single message
            full_context = "\n\n".join(contexts)
            log(f"total context size = {len(full_context)} chars")

            # Create system message at position 0 (before user messages)
            context_message = {"role": "system", "content": full_context}

            body.setdefault("messages", []).insert(0, context_message)

            log("injected system message at position 0")
            log(f"final messages array has {len(body['messages'])} items:")
            for i, msg in enumerate(body["messages"]):
                role = msg.get("role", "UNKNOWN")
                preview = str(msg.get("content", "")).replace("\n", " ")[:100]
                log(f"  [{i}] role={role}, preview='{preview}...'")

            return body

        except (ConnectionExceptionError, NoConnectionError) as e:
            log_err(f"connection failed - {e}")
        except Exception as e:
            log_err(
                f"error building context - "
                f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            )
        return body
