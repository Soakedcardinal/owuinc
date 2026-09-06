"""
title: owuinc
author: soakedcardinal
git_url: https://github.com/soakedcardinal/owuinc
description: Manage files, tasks, and calendars via WebDAV and CalDAV.
requirements: caldav>=3.0.0,icalendar,aiowebdav2
version: 3.7.3
license: MIT
"""

import asyncio
import fnmatch
import functools
import inspect
import logging
import os
import re
import urllib.parse
import uuid
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from typing import Callable
from zoneinfo import ZoneInfo

import tiktoken
from aiowebdav2 import Client as WebDAVClient
from aiowebdav2.client import ClientOptions
from aiowebdav2.exceptions import (
    ConnectionExceptionError,
    NoConnectionError,
    RemoteResourceNotFoundError,
)
from caldav.aio import get_async_davclient
from caldav.lib.error import NotFoundError
from dateutil.rrule import rrulestr
from icalendar import Alarm, Event
from pydantic import BaseModel, Field

_logger = logging.getLogger("owuinc")
_logger.addHandler(logging.StreamHandler())
_logger.setLevel(logging.DEBUG)

_tokenizer = tiktoken.get_encoding("cl100k_base")


def _count_tokens(text: str) -> int:
    """Count tokens using tiktoken's cl100k_base encoder."""
    return len(_tokenizer.encode(text))


# ============================================================
# PATTERN CONSTANTS
# ============================================================

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_URL_RE = re.compile(r"https?://\S+")
_CONNECTION_EXC = (
    ConnectionExceptionError,
    NoConnectionError,
    ConnectionError,
    TimeoutError,
)


# ============================================================
# SANITIZATION & ERROR REPORTING
# ============================================================


def _sanitize(msg: str) -> str:
    """Strip URLs, UUIDs, and WebDAV paths from error messages to prevent info leaks."""
    msg = _URL_RE.sub("<url>", msg)
    msg = _UUID_RE.sub("<uuid>", msg)
    msg = re.sub(r"/remote\.php/dav/files/[^/\s]+(?:/[^/\s]*)*", "<path>", msg)
    return msg


def _format_args(func: Callable, args: tuple, kwargs: dict) -> str:
    """Format function arguments for status/debug display."""
    try:
        sig = inspect.signature(func)
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        parts = []
        for name, val in bound.arguments.items():
            if name in ("self", "__user__", "__event_emitter__"):
                continue
            s = repr(val)
            if len(s) > 120:
                s = s[:117] + "..."
            parts.append(f"{name}={s}")
        return ", ".join(parts)
    except Exception:
        return ""


def _format_result(response: dict) -> str:
    """Format a tool response for status display."""
    if response.get("result") == "False":
        return response.get("details", "error")
    data = response.get("data")
    if data is None:
        return "Done"
    if isinstance(data, list):
        return f"{len(data)} items" if data else "empty"
    if isinstance(data, dict):
        if "matches" in data:
            m, s = len(data["matches"]), len(data.get("skipped", []))
            return f"{m} match{'es' if m != 1 else ''}" + (
                f" ({s} skipped)" if s else ""
            )
        return str(data.get("summary", "Done"))
    if isinstance(data, str):
        return data[:120] + "..." if len(data) > 120 else data
    return "Done"


def _token_suffix(data) -> str:
    """Return ' N tokens' suffix for responses with significant content."""
    if isinstance(data, str):
        return f" {_count_tokens(data)} tokens"
    if isinstance(data, list):
        combined = "\n".join(
            str(item.get("summary", item)) if isinstance(item, dict) else str(item)
            for item in data
        )
        if combined:
            return f" {_count_tokens(combined)} tokens"
    return ""


def _token_line(op: str, data) -> str:
    """Return a token-count line for operations that return content."""
    if op in ("cat", "tasks", "calendar_events"):
        return _token_suffix(data).strip()
    return ""


def _format_status(op: str, kwargs: dict, response: dict) -> str:
    """Format a bash-style command string for status display."""
    data = response.get("data")
    if response.get("result") == "False":
        return f"{op}: {response.get('details', 'error')}"

    if op == "cat":
        path = kwargs.get("path", "")
        lines = ""
        if kwargs.get("offset") is not None:
            lines = f" -o {kwargs['offset']}"
            if kwargs.get("limit") is not None:
                lines += f" -l {kwargs['limit']}"
        cmd = f"cat{lines} {path}"
        tok = _token_line(op, data)
        return f"{cmd}\n{tok}" if tok else cmd

    if op == "write":
        path = kwargs.get("path", "")
        return f"write {path}"

    if op == "append":
        path = kwargs.get("path", "")
        return f"append {path}"

    if op == "edit":
        path = kwargs.get("file_path", "")
        return f"edit {path}"

    if op == "mkdir":
        path = kwargs.get("path", "")
        return f"mkdir -p {path}"

    if op == "ls":
        path = kwargs.get("path") or "."
        flags = ""
        if kwargs.get("detail"):
            flags = " -la"
        return f"ls{flags} {path}"

    if op == "find":
        pattern = kwargs.get("pattern", "")
        path = kwargs.get("path")
        loc = f" {path}" if path else ""
        return f"find {pattern}{loc}"

    if op == "grep":
        pattern = kwargs.get("pattern", "")
        path = kwargs.get("path")
        include = kwargs.get("include")
        flags = "-r"
        inc = f" --include={include}" if include else ""
        loc = f" {path}" if path else ""
        matches = len(data.get("matches", [])) if isinstance(data, dict) else 0
        return f"grep {flags}{inc} '{pattern}'{loc}  # {matches} matches"

    if op == "stat":
        return f"stat {kwargs.get('path', '')}"

    if op == "tree":
        path = kwargs.get("path") or "."
        depth = kwargs.get("depth") or 3
        return f"tree -L {depth} {path}"

    if op == "rm":
        paths = kwargs.get("paths", [])
        paths_str = " ".join(paths) if isinstance(paths, list) else str(paths)
        out = f"rm -rf {paths_str}"
        if isinstance(data, list):
            failed = sum(
                1 for d in data if isinstance(d, dict) and d.get("result") == "False"
            )
            if failed:
                out += f"  # {failed} failed"
        return out

    if op == "mv":
        src, dst = kwargs.get("src", ""), kwargs.get("dst", "")
        return f"mv {src} {dst}"

    if op == "cp":
        src, dst = kwargs.get("src", ""), kwargs.get("dst", "")
        return f"cp -r {src} {dst}"

    if op == "calendars":
        n = len(data) if isinstance(data, list) else 0
        return f"calendars  # {n} calendars"

    if op == "task_lists":
        n = len(data) if isinstance(data, list) else 0
        return f"task_lists  # {n} lists"

    if op == "tasks":
        list_name = kwargs.get("list_name") or "(default)"
        cmd = f"tasks {list_name}"
        tok = _token_line(op, data)
        return f"{cmd}\n{tok}" if tok else cmd

    if op == "add_task":
        summary = kwargs.get("summary", "")
        list_name = kwargs.get("list_name") or "(default)"
        return f"add_task '{summary}' {list_name}"

    if op == "edit_task":
        target = kwargs.get("summary") or kwargs.get("uid", "(unknown)")
        list_name = kwargs.get("list_name") or "(default)"
        return f"edit_task '{target}' {list_name}"

    if op == "complete_task":
        target = kwargs.get("summary") or kwargs.get("uid", "(unknown)")
        list_name = kwargs.get("list_name") or "(default)"
        return f"complete_task '{target}' {list_name}"

    if op == "delete_task":
        target = kwargs.get("summary") or kwargs.get("uid", "(unknown)")
        list_name = kwargs.get("list_name") or "(default)"
        return f"delete_task '{target}' {list_name}"

    if op == "create_calendar_event":
        summary = kwargs.get("summary", "")
        cal = kwargs.get("calendar_name") or "(default)"
        return f"create_event '{summary}' {cal}"

    if op == "edit_calendar_event":
        target = kwargs.get("summary") or kwargs.get("uid", "(unknown)")
        cal = kwargs.get("calendar_name") or "(default)"
        return f"edit_event '{target}' {cal}"

    if op == "calendar_events":
        cal = kwargs.get("calendar_name") or "(default)"
        cmd = f"calendar_events {cal}"
        tok = _token_line(op, data)
        return f"{cmd}\n{tok}" if tok else cmd

    if op == "delete_calendar_event":
        target = kwargs.get("summary") or kwargs.get("uid", "(unknown)")
        cal = kwargs.get("calendar_name") or "(default)"
        return f"delete_event '{target}' {cal}"

    return f"{op}: {_format_result(response)}"


async def _emit(emitter, event: dict):
    """Emit an event through the OpenWebUI event emitter."""
    if not emitter:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    await emitter(event)


# ============================================================
# ERROR-WRAPPING DECORATORS
# ============================================================


def _safe(func: Callable) -> Callable:
    """Unified decorator: catches ALL exceptions, returns dict, never raises.

    - Emits detailed status events with args + results to UI.
    - Logs to container stderr (verbosity controlled by DEBUG_MODE valve).
    - Connection/timeout errors return generic "connection error" (type in debug).
    - All other exceptions surface sanitized str(e) for actionable diagnostics.
    """

    @functools.wraps(func)
    async def wrapper(*args, **kwargs) -> dict:
        op = func.__name__

        valves = None
        if args and hasattr(args[0], "valves"):
            valves = args[0].valves
        emitter = kwargs.get("__event_emitter__")

        debug = (
            bool(valves.DEBUG_MODE)
            if valves and hasattr(valves, "DEBUG_MODE")
            else False
        )

        arg_str = _format_args(func, args, kwargs)
        if debug and arg_str:
            _logger.info(f"{op}({arg_str}): starting")
        elif not debug:
            _logger.debug(f"{op}: starting")

        t0 = asyncio.get_event_loop().time()

        try:
            result = func(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            response = {"result": "True"}
            if result is not None:
                response["data"] = result

            elapsed = round((asyncio.get_event_loop().time() - t0) * 1000)
            res_str = _format_result(response)
            if debug:
                _logger.info(f"{op}: success ({elapsed}ms) → {res_str}")
            else:
                _logger.info(f"{op}: success")

            desc = _format_status(op, kwargs, response)
            asyncio.create_task(
                _emit(
                    emitter,
                    {
                        "type": "status",
                        "data": {
                            "description": desc,
                            "done": True,
                        },
                    },
                )
            )
            return response

        except Exception as e:
            elapsed = round((asyncio.get_event_loop().time() - t0) * 1000)
            if isinstance(e, _CONNECTION_EXC):
                details = (
                    f"connection error ({type(e).__name__})"
                    if debug
                    else "connection error"
                )
            else:
                details = _sanitize(str(e)) or _sanitize(type(e).__name__)

            if debug:
                _logger.warning(f"{op}: error ({elapsed}ms) → {details}", exc_info=True)
            else:
                _logger.warning(f"{op}: error → {details}")

            asyncio.create_task(
                _emit(
                    emitter,
                    {
                        "type": "status",
                        "data": {
                            "description": f"{op}: {details}",
                            "done": True,
                        },
                    },
                )
            )

            if isinstance(e, _CONNECTION_EXC):
                asyncio.create_task(
                    _emit(
                        emitter,
                        {
                            "type": "notification",
                            "data": {"content": f"{op}: connection error"},
                        },
                    )
                )

            return {"result": "False", "details": details}

    return wrapper


def caldav_safe(func: Callable) -> Callable:
    return _safe(func)


def webdav_safe(func: Callable) -> Callable:
    return _safe(func)


# ============================================================
# ReDoS PROTECTION
# ============================================================


def _check_redos_risk(pattern: str) -> None:
    """Raise ValueError if pattern contains nested quantifiers that can cause ReDoS."""
    from re import _parser as re_parser  # type: ignore[attr-defined]
    from re._constants import _NamedIntConstant  # type: ignore[attr-defined]

    def _token_name(t):
        return t.name if isinstance(t, _NamedIntConstant) else str(t)

    parsed = list(re_parser.parse(pattern, re.VERBOSE))
    _Q = {"MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"}

    def _has_nested(tokens, inside_q=False):
        for tt, tv in tokens:
            tn = _token_name(tt)
            if tn in _Q:
                if inside_q:
                    return True
                if _has_nested(tv[2], inside_q=True):
                    return True
            elif tn == "SUBPATTERN":
                if _has_nested(tv[3], inside_q):
                    return True
            elif tn == "BRANCH":
                for branch in tv[1]:
                    if _has_nested(branch, inside_q):
                        return True
        return False

    if _has_nested(parsed):
        raise ValueError("nested quantifiers are not allowed")


# ============================================================
# PATH HELPERS
# ============================================================


def _webdav_path(p: str) -> str:
    """Ensure path has leading / for aiowebdav2."""
    return p if p.startswith("/") else "/" + p


def _strip_leading_slash(p: str) -> str:
    """Strip leading / from aiowebdav2 paths to match webdavclient3 format."""
    return p.lstrip("/") if p else p


def validate_path(path, valves):
    """Validate and normalize file paths for WebDAV operations.

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


# ============================================================
# REMINDER PARSING
# ============================================================


def parse_reminders(reminders: list | None = None) -> list:
    """Parse reminder strings like '15min', '1h', '3d' into dicts."""
    if not reminders:
        return []

    parsed = []
    for r in reminders:
        minutes = 0
        matched = False

        if r in ("0", "0min", "0 min"):
            matched = True
        elif r.endswith(("min", "mins", "minutes")):
            m = re.search(r"\d+", r)
            if m is not None:
                minutes = int(m.group())
                matched = True
        elif r.endswith(("h", "hr", "hour", "hours")):
            m = re.search(r"\d+", r)
            if m is not None:
                minutes = int(m.group()) * 60
                matched = True
        elif r.endswith(("d", "day", "days")):
            m = re.search(r"\d+", r)
            if m is not None:
                minutes = int(m.group()) * 1440
                matched = True

        if not matched:
            raise ValueError(f"unrecognized reminder format: {r!r}")
        parsed.append({"minutes": minutes, "action": "DISPLAY"})
    return parsed


# ============================================================
# WHITELIST / BLACKLIST CHECKS
# ============================================================


def is_whitelisted(whitelist: str, item: str) -> bool:
    """Check if item is in comma-separated whitelist."""
    if not whitelist:
        return False
    return item in {s.strip() for s in whitelist.split(",") if s.strip()}


def is_blacklisted(blacklist: str, path: str) -> bool:
    """Check if path is under any blacklisted directory prefix.

    Blacklist entries are normalized (leading/trailing slashes stripped)
    so "secret", "secret/", and "/secret" are all equivalent.
    """
    if not blacklist:
        return False
    cleaned = {s.strip().strip("/") for s in blacklist.split(",") if s.strip()}
    cleaned.discard("")
    for prefix in cleaned:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


# ============================================================
# MAIN TOOLS CLASS
# ============================================================


class Tools:
    valves: "Valves"

    def __init__(self):
        self.valves = self.Valves()

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
                "Directory for all file operations. Leading `/` is optional and will be stripped. Directory is auto-created if missing. Leave empty to use Nextcloud root."
            ),
        )
        DEFAULT_CALENDAR: str = Field(
            default="Personal", description="Default calendar for event operations"
        )
        DEFAULT_TASK_LIST: str = Field(
            default="Tasks", description="Default task list for task operations"
        )
        CALENDAR_WHITELIST: str = Field(
            default="Personal",
            description=(
                "Comma-separated list of allowed calendars (default-deny: only listed calendars are accessible)."
            ),
        )
        TASK_LIST_WHITELIST: str = Field(
            default="Tasks",
            description=(
                "Comma-separated list of allowed task lists (default-deny: only listed task lists are accessible)."
            ),
        )
        FILE_BLACKLIST: str = Field(
            default="",
            description=(
                "Comma-separated paths (relative to sandbox root) to exclude from all file operations. SECURITY NOTE: uses default-allow semantics (empty = no restrictions). The primary file boundary is SANDBOX_DIR. CALENDAR_WHITELIST and TASK_LIST_WHITELIST use default-deny semantics — only explicitly listed calendars/task lists are accessible."
            ),
        )
        DEBUG_MODE: bool = Field(
            default=False,
            description=(
                "Verbose container logging: full args, paths, timing, and exception tracebacks in docker logs. Toggleable at runtime — no restart needed."
            ),
        )
        WEBDAV_TIMEOUT: int = Field(
            default=10,
            ge=1,
            le=120,
            description="WebDAV request timeout in seconds (1-120)",
        )
        CALDAV_TIMEOUT: int = Field(
            default=10,
            ge=1,
            le=120,
            description="CalDAV request timeout in seconds (1-120)",
        )

    # -- Client factories --

    def _webdav_client(self):
        """Create a WebDAV client configured from valves."""
        base = self.valves.NEXTCLOUD_BASE_URL
        wd_user = self.valves.WEBDAV_USERNAME
        url = f"{base}/remote.php/dav/files/{wd_user}/"
        from aiohttp import ClientTimeout

        return WebDAVClient(
            url,
            self.valves.NEXTCLOUD_USERNAME,
            self.valves.NEXTCLOUD_APP_PASSWORD,
            options=ClientOptions(
                timeout=ClientTimeout(total=self.valves.WEBDAV_TIMEOUT)
            ),
        )

    async def _caldav_client(self):
        """Create a CalDAV client configured from valves."""
        base = self.valves.NEXTCLOUD_BASE_URL
        url = f"{base}/remote.php/dav"
        return await get_async_davclient(
            username=self.valves.NEXTCLOUD_USERNAME,
            password=self.valves.NEXTCLOUD_APP_PASSWORD,
            url=url,
            features="nextcloud",
            enable_rfc6764=False,
            timeout=self.valves.CALDAV_TIMEOUT,
        )

    # -- Internal helpers --

    async def _get_calendar(self, principal, calendar_name: str):
        """Get a calendar by name, working around caldav.aio's broken calendar().

        caldav.aio.CalendarSet.calendar(name=...) calls get_calendars() and
        get_display_name() internally without awaiting them, which fails for
        async clients. This helper properly awaits all async calls.

        Raises NotFoundError if no match or multiple matches found.
        """
        calendars = await principal.get_calendars()
        matches = []
        for cal in calendars:
            display_name = await cal.get_display_name()
            if display_name == calendar_name:
                matches.append(cal)
        if len(matches) > 1:
            raise NotFoundError(f"multiple calendars named {calendar_name!r}")
        if len(matches) == 1:
            return matches[0]
        raise NotFoundError(f"No calendar with name {calendar_name!r} found")

    async def _resolve_task_uid(self, cal, identifier: str) -> str:
        """Resolve a task identifier to its UID.

        If identifier is a UUID, verify it exists before returning.
        Otherwise, look up the task by summary and return its UID.
        Raises if not found or multiple tasks match the summary.
        """
        todos = await cal.todos()
        todo_map = {str(t.component["uid"]): t for t in todos}
        try:
            uuid.UUID(identifier)
        except (ValueError, AttributeError):
            pass
        else:
            if identifier not in todo_map:
                raise Exception(f"task with uid {identifier!r} not found")
            return identifier
        summary_matches = []
        norm_identifier = identifier.strip()
        for todo in todos:
            if norm_identifier == str(todo.component["summary"]).strip():
                summary_matches.append(str(todo.component["uid"]))
        if len(summary_matches) > 1:
            raise Exception(f"Multiple matches for {identifier!r}: {summary_matches}")
        if len(summary_matches) == 1:
            return summary_matches[0]
        raise Exception(f"Parent task with summary {identifier!r} not found")

    async def _find_task_by_uid_or_summary(
        self, cal, uid: str | None, summary: str | None
    ):
        """Find a task by uid or summary. Raises if not found or ambiguous."""
        if uid:
            return await cal.todo_by_uid(uid)
        if summary is not None:
            matches = []
            norm_summary = summary.strip().lower()
            for todo in await cal.todos():
                if norm_summary == str(todo.component["summary"]).strip().lower():
                    matches.append(todo.component["uid"])
            if len(matches) > 1:
                raise Exception(f"Multiple matches for {summary!r}: {matches}")
            if len(matches) == 1:
                return await cal.todo_by_uid(matches[0])
        raise Exception("task not found")

    async def _find_event_by_uid_or_summary(
        self, cal, uid: str | None, summary: str | None
    ):
        """Find an event by uid or summary. Raises if not found or ambiguous."""
        if uid:
            return await cal.event_by_uid(uid)
        if summary is not None:
            matches = []
            norm_summary = summary.strip().lower()
            for e in await cal.events():
                if norm_summary == str(e.component["summary"]).strip().lower():
                    matches.append(e.component["uid"])
            if len(matches) > 1:
                raise NotFoundError("multiple matches")
            if len(matches) == 1:
                return await cal.event_by_uid(matches[0])
        raise NotFoundError("event not found")

    # -- Sandbox & blacklist helpers --

    async def _ensure_sandbox(self, client):
        """Ensure sandbox directory exists (must be called within webdav context)."""
        sandbox = self.valves.SANDBOX_DIR.strip().rstrip("/")
        if sandbox:
            try:
                await client.list_files(_webdav_path(sandbox + "/"))
            except RemoteResourceNotFoundError:
                await client.mkdir(_webdav_path(sandbox))

    def _check_blacklisted(self, rel_path: str) -> None:
        """Raise ValueError if rel_path (relative to sandbox) is blacklisted."""
        rel_path = rel_path.strip("/")
        if rel_path and is_blacklisted(self.valves.FILE_BLACKLIST, rel_path):
            raise ValueError("Access denied")

    async def _check_blacklisted_recursive(self, client, full_path: str) -> None:
        """Raise ValueError if full_path or any descendant is blacklisted."""
        rel_path = self._get_rel_path(full_path).strip("/")
        self._check_blacklisted(rel_path)
        if not self.valves.FILE_BLACKLIST:
            return
        try:
            is_dir = await client.is_dir(_webdav_path(full_path))
        except Exception:
            return
        if not is_dir:
            return
        items = await client.list_files(_webdav_path(full_path))
        for item in items:
            item_stripped = _strip_leading_slash(item).rstrip("/")
            if item_stripped == _strip_leading_slash(full_path).rstrip("/"):
                continue
            item_rel = self._get_rel_path(item_stripped).strip("/")
            self._check_blacklisted(item_rel)
            try:
                if not await client.is_dir(_webdav_path(item_stripped)):
                    continue
            except Exception:
                continue
            await self._check_blacklisted_recursive(client, item_stripped)

    def _is_result_blacklisted(self, rel_path: str) -> bool:
        """Check if a result path (relative to sandbox) should be hidden."""
        rel_path = rel_path.strip("/")
        return bool(rel_path) and is_blacklisted(self.valves.FILE_BLACKLIST, rel_path)

    @property
    def sandbox_prefix(self) -> str:
        """Return the sandbox prefix string (e.g., 'owuinc/')."""
        return self.valves.SANDBOX_DIR.strip().rstrip("/") + "/"

    def _get_rel_path(self, full_path: str) -> str:
        """Convert a sandbox-prefixed full_path to a relative path."""
        if full_path.startswith(self.sandbox_prefix):
            return full_path[len(self.sandbox_prefix) :]
        return full_path

    # -- Display formatting helpers --

    def _format_size(self, size_str: str) -> str:
        """Format file size to human-readable string."""
        if size_str is None:
            return "n/a"
        try:
            size: float = int(size_str)
        except (ValueError, TypeError):
            return size_str
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if abs(size) < 1024:
                return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"

    def _format_datetime(self, dt_str: str) -> str:
        """Format WebDAV datetime string to a readable format."""
        if not dt_str:
            return "n/a"
        for fmt in (
            "%a, %d %b %Y %H:%M:%S %Z",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
        ):
            try:
                parsed = datetime.strptime(dt_str.replace(" +0000", "+00:00"), fmt)
                return parsed.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
        try:
            parsed = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            return parsed.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return dt_str

    # ============================================================
    # CALDAV LIST OPERATIONS
    # ============================================================

    @caldav_safe
    async def calendars(self, __event_emitter__=None) -> list[str]:
        """Retrieve available calendars (unique display names)."""
        client = await self._caldav_client()
        try:
            principal = await client.principal()
            calendars = await principal.get_calendars()
            seen = set()
            result = []
            for cal in calendars:
                cal_name = await cal.get_display_name()
                if cal_name and is_whitelisted(
                    self.valves.CALENDAR_WHITELIST, cal_name
                ):
                    if cal_name not in seen:
                        result.append(cal_name)
                        seen.add(cal_name)
            return result
        finally:
            await client.close()

    @caldav_safe
    async def task_lists(self, __event_emitter__=None) -> list[str]:
        """Retrieve available task lists (unique display names)."""
        client = await self._caldav_client()
        try:
            principal = await client.principal()
            calendars = await principal.get_calendars()
            seen = set()
            result = []
            for cal in calendars:
                tl = await cal.get_display_name()
                if tl and is_whitelisted(self.valves.TASK_LIST_WHITELIST, tl):
                    if tl not in seen:
                        result.append(tl)
                        seen.add(tl)
            return result
        finally:
            await client.close()

    # ============================================================
    # WEBDAV FILE OPERATIONS
    # ============================================================

    @webdav_safe
    async def mkdir(self, path: str, __event_emitter__=None) -> None:
        """Create a directory, including parents (mkdir -p semantics)."""
        full_path = validate_path(path, self.valves)
        self._check_blacklisted(self._get_rel_path(full_path))
        client = self._webdav_client()
        try:
            await self._ensure_sandbox(client)
            await client.mkdir(_webdav_path(full_path), recursive=True)
        finally:
            await client.close()

    @webdav_safe
    async def ls(
        self, path: str | None = None, detail: bool = False, __event_emitter__=None
    ) -> list[str]:
        """List files and directories (sandbox-relative names). Set detail=True for size/type/modified, bash ls -la style."""
        full_path = validate_path(path, self.valves)
        self._check_blacklisted(self._get_rel_path(full_path))
        client = self._webdav_client()
        try:
            await self._ensure_sandbox(client)
            prefix = self.sandbox_prefix

            if detail:
                raw_items = await client.list_with_infos(_webdav_path(full_path))
                result_list = []
                for item in raw_items:
                    full_item_path = _strip_leading_slash(item.get("path", "")).rstrip(
                        "/"
                    )
                    if full_item_path == _strip_leading_slash(full_path).rstrip("/"):
                        continue
                    rel_item = (
                        full_item_path[len(prefix) :]
                        if full_item_path.startswith(prefix)
                        else os.path.basename(full_item_path)
                    )
                    if self._is_result_blacklisted(rel_item):
                        continue
                    is_dir = str(item.get("isdir", "False")).lower() == "true"
                    name = item.get("name") or os.path.basename(full_item_path)
                    content_type = item.get("content_type") or "-"
                    size_str = self._format_size(item.get("size", "0"))
                    modified = self._format_datetime(item.get("modified", ""))
                    created = self._format_datetime(item.get("created", ""))
                    if is_dir:
                        perms = "drwxr-xr-x"
                        size_str = "       -"
                        content_type = "-"
                        display = f"{name}/"
                    else:
                        perms = "-rw-r--r--"
                        display = name
                    result_list.append(
                        f"{perms} {size_str:>10} {content_type:<18} {modified}  {display}"
                    )
                    if created != "n/a":
                        result_list[-1] += f" (created: {created})"
                return result_list

            # Simple listing mode.
            raw_paths = await client.list_files(_webdav_path(full_path))
            paths = [_strip_leading_slash(rp).rstrip("/") for rp in raw_paths]
            parent = _strip_leading_slash(full_path).rstrip("/")
            result_list = []
            for item in paths:
                if item == parent:
                    continue
                if item.startswith(prefix):
                    item = item[len(prefix) :]
                if not self._is_result_blacklisted(item):
                    result_list.append(item)
            return result_list
        finally:
            await client.close()

    @webdav_safe
    async def find(
        self, pattern: str, path: str | None = None, __event_emitter__=None
    ) -> list[str]:
        """Find files by glob pattern (e.g. '**/*.py'). Supports brace expansion."""
        target_dir = validate_path(path if path else "", self.valves)
        rel_path = self._get_rel_path(target_dir)
        self._check_blacklisted(rel_path)

        client = self._webdav_client()
        try:
            await self._ensure_sandbox(client)

            pattern_parts = pattern.split("/")
            is_recursive = (
                "**" in pattern_parts
                or "/" in pattern
                or (len(pattern_parts) == 1 and "*" in pattern_parts[0])
            )

            all_files = await client.list_with_infos(
                _webdav_path(target_dir), recursive=is_recursive
            )
            files_only = [
                f for f in all_files if str(f.get("isdir", "False")).lower() != "true"
            ]

            # Expand brace syntax: "foo.{py,js}" -> ["foo.py", "foo.js"]
            patterns_to_match = [pattern]
            if "{" in pattern and "}" in pattern:
                start = pattern.find("{")
                end = pattern.find("}", start)
                if end != -1:
                    prefix, suffix = pattern[:start], pattern[end + 1 :]
                    alternatives = pattern[start + 1 : end].split(",")
                    patterns_to_match = [prefix + alt + suffix for alt in alternatives]

            target_root = target_dir.rstrip("/")
            matched = []
            for file_info in files_only:
                raw_path = _strip_leading_slash(file_info.get("path", ""))
                # Normalize path: aiowebdav2 may return full WebDAV paths or
                # sandbox-relative paths. If target_root is already present, use as-is.
                if raw_path.startswith(target_root + "/") or raw_path == target_root:
                    full_path = raw_path
                elif target_root + "/" in raw_path:
                    full_path = raw_path
                else:
                    full_path = target_root + "/" + raw_path
                filename = os.path.basename(full_path)

                # Compute sandbox-relative path.
                if self.sandbox_prefix in full_path:
                    sandbox_rel = full_path.split(self.sandbox_prefix, 1)[1]
                else:
                    sandbox_rel = (
                        full_path[len(target_root) + 1 :]
                        if full_path.startswith(target_root + "/")
                        else filename
                    )

                # Compute path relative to target directory.
                if sandbox_rel.startswith(rel_path + "/"):
                    rel_to_target = (
                        sandbox_rel[len(rel_path) + 1 :] if rel_path else sandbox_rel
                    )
                elif rel_path:
                    rel_to_target = filename
                else:
                    rel_to_target = sandbox_rel

                for pat in patterns_to_match:
                    # Split pattern into directory prefix and name pattern.
                    if "/**/" in pat:
                        dir_prefix, pattern_name = pat.split("/**/", 1)
                    elif pat.startswith("**/"):
                        dir_prefix = "**"
                        pattern_name = pat[3:]
                    elif "/" in pat:
                        parts = pat.rsplit("/", 1)
                        dir_prefix = parts[0]
                        pattern_name = parts[1]
                    else:
                        dir_prefix = ""
                        pattern_name = pat

                    # Enforce directory scope from pattern.
                    if dir_prefix and dir_prefix != "**":
                        if "/**" in dir_prefix:
                            base = dir_prefix.split("/**")[0]
                            if not rel_to_target.startswith(base + "/"):
                                continue
                        else:
                            if not rel_to_target.startswith(dir_prefix + "/"):
                                continue
                            remaining = rel_to_target[len(dir_prefix) + 1 :]
                            if "/" in remaining:
                                continue

                    if fnmatch.fnmatch(filename, pattern_name):
                        matched.append(
                            {
                                "path": full_path,
                                "modified": file_info.get("modified", ""),
                            }
                        )
                        break

            try:
                matched.sort(key=lambda x: x.get("modified", ""))
            except Exception:
                pass

            result = []
            for f in matched:
                full_path = f["path"]
                if self.sandbox_prefix in full_path:
                    rel = full_path.split(self.sandbox_prefix, 1)[1]
                elif rel_path and full_path.startswith(rel_path + "/"):
                    rel = full_path[len(rel_path) + 1 :]
                elif not rel_path:
                    rel = full_path
                else:
                    rel = os.path.basename(full_path)

                if self._is_result_blacklisted(rel):
                    continue
                result.append(rel)

            return result
        finally:
            await client.close()

    @webdav_safe
    async def grep(
        self,
        pattern: str,
        path: str | None = None,
        include: str | None = None,
        __event_emitter__=None,
    ) -> dict:
        """Search file contents with regex, recursively. Use include for filter (e.g. '*.py', braces ok: '*.{py,js}').
        Text files only — binary files are skipped and listed in the result. Nested quantifiers (e.g. '(a+)+') are rejected.
        """
        target_dir = validate_path(path if path else "", self.valves)
        search_rel = self._get_rel_path(target_dir)
        self._check_blacklisted(search_rel)

        client = self._webdav_client()
        try:
            await self._ensure_sandbox(client)

            all_items = await client.list_with_infos(
                _webdav_path(target_dir), recursive=True
            )
            if not all_items:
                return {"matches": [], "skipped": [], "summary": "no files found"}

            file_list = [
                _strip_leading_slash(item.get("path", item))
                for item in all_items
                if str(item.get("isdir", "False")).lower() != "true"
            ]

            # Expand brace syntax on include filter.
            patterns_to_match = [include] if include else []
            if include and "{" in include and "}" in include:
                start = include.find("{")
                end = include.find("}", start)
                if end != -1:
                    prefix, suffix = include[:start], include[end + 1 :]
                    alternatives = include[start + 1 : end].split(",")
                    patterns_to_match = [prefix + alt + suffix for alt in alternatives]

            try:
                compiled_regex = re.compile(pattern)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern: {e}")
            _check_redos_risk(pattern)

            results = []
            skipped = []
            for full_path in file_list:
                if self.sandbox_prefix in full_path:
                    rel = full_path.split(self.sandbox_prefix, 1)[1]
                elif full_path.startswith(search_rel + "/"):
                    rel = full_path[len(search_rel) + 1 :]
                else:
                    rel = os.path.basename(full_path)

                if self._is_result_blacklisted(rel):
                    continue

                filename = os.path.basename(rel)

                # Apply include filter.
                if patterns_to_match:
                    matched = False
                    for pat in patterns_to_match:
                        pattern_name = pat.split("/")[-1] if "/" in pat else pat
                        if fnmatch.fnmatch(filename, pattern_name):
                            matched = True
                            break
                    if not matched:
                        continue

                webdav_path = _webdav_path(validate_path(rel, self.valves))

                buf = BytesIO()
                try:
                    await client.resource(webdav_path).read_from(buf)
                except Exception:
                    continue

                try:
                    content = buf.getvalue().decode("utf-8")
                except UnicodeDecodeError:
                    skipped.append(rel)
                    continue

                for line_num, line in enumerate(content.splitlines(), start=1):
                    if compiled_regex.search(line):
                        results.append(
                            {
                                "file": rel,
                                "line": line_num,
                                "content": line.strip(),
                            }
                        )

            results.sort(key=lambda x: (x["file"], x["line"]))
            return {
                "matches": results,
                "skipped": skipped,
                "summary": f"{len(results)} matches in {len(file_list)} files",
            }
        finally:
            await client.close()

    @webdav_safe
    async def write(
        self, path: str, content: str | None = None, __event_emitter__=None
    ) -> None:
        """Write to a file, overwriting existing content. Creates if missing."""
        if content is None:
            content = ""
        full_path = validate_path(path, self.valves)
        self._check_blacklisted(self._get_rel_path(full_path))
        client = self._webdav_client()
        try:
            await self._ensure_sandbox(client)
            await client.resource(_webdav_path(full_path)).write_to(
                BytesIO(content.encode("utf-8"))
            )
        finally:
            await client.close()

    @webdav_safe
    async def cat(
        self,
        path: str,
        offset: int | None = None,
        limit: int | None = None,
        __event_emitter__=None,
    ) -> str:
        """Read a file (UTF-8 text; binary files are rejected). offset: 1-based first line, limit: max lines."""
        if not path:
            raise ValueError("path cannot be empty")
        if offset is not None and offset < 1:
            raise ValueError(f"offset must be >= 1, got {offset}")
        if limit is not None and limit <= 0:
            raise ValueError(f"limit must be > 0, got {limit}")

        full_path = validate_path(path, self.valves)
        self._check_blacklisted(self._get_rel_path(full_path))
        client = self._webdav_client()
        try:
            await self._ensure_sandbox(client)
            buf = BytesIO()
            await client.resource(_webdav_path(full_path)).read_from(buf)
            try:
                lines = buf.getvalue().decode("utf-8").splitlines()
            except UnicodeDecodeError:
                raise ValueError("not a text file")

            if offset is not None:
                lines = lines[max(0, offset - 1) :]
            if limit is not None:
                lines = lines[:limit]

            return "\n".join(lines)
        finally:
            await client.close()

    @webdav_safe
    async def append(
        self, path: str, content: str | None = None, __event_emitter__=None
    ) -> None:
        """Append content to a file. Creates if missing.
        Uses WebDAV lock to prevent concurrent read-modify-write conflicts.
        """
        if content is None:
            content = ""
        full_path = validate_path(path, self.valves)
        self._check_blacklisted(self._get_rel_path(full_path))
        client = self._webdav_client()
        try:
            await self._ensure_sandbox(client)
            res_path = _webdav_path(full_path)
            try:
                lock = await client.lock(res_path, timeout=30)
                async with lock as locked:
                    res = locked.resource(res_path)
                    try:
                        buf = BytesIO()
                        await res.read_from(buf)
                        try:
                            existing = buf.getvalue().decode("utf-8")
                        except UnicodeDecodeError:
                            raise ValueError("not a text file")
                    except RemoteResourceNotFoundError:
                        existing = ""
                    if existing and not existing.endswith("\n"):
                        content = "\n" + content
                    await res.write_to(BytesIO((existing + content).encode("utf-8")))
            except RemoteResourceNotFoundError:
                await client.resource(res_path).write_to(
                    BytesIO(content.encode("utf-8"))
                )
        finally:
            await client.close()

    @webdav_safe
    async def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
        __event_emitter__=None,
    ) -> None:
        """Exact string replacement. Requires unique match unless replace_all=True.
        Uses WebDAV lock to prevent concurrent read-modify-write conflicts.
        """
        if not old_string:
            raise ValueError("old_string cannot be empty")
        if old_string == new_string:
            raise ValueError("old_string and new_string must be different")

        full_path = validate_path(file_path, self.valves)
        self._check_blacklisted(self._get_rel_path(full_path))
        client = self._webdav_client()
        try:
            await self._ensure_sandbox(client)
            res_path = _webdav_path(full_path)
            try:
                lock = await client.lock(res_path, timeout=30)
            except RemoteResourceNotFoundError:
                raise ValueError("file not found")
            async with lock as locked:
                buf = BytesIO()
                await locked.resource(res_path).read_from(buf)
                try:
                    content = buf.getvalue().decode("utf-8")
                except UnicodeDecodeError:
                    raise ValueError("not a text file")

                count = content.count(old_string)
                if count == 0:
                    raise ValueError("String not found")
                if count > 1 and not replace_all:
                    raise ValueError(f"Found {count} matches, but replace_all is false")

                replacement_count = 1 if not replace_all else -1
                modified = content.replace(old_string, new_string, replacement_count)
                await locked.resource(res_path).write_to(
                    BytesIO(modified.encode("utf-8"))
                )
        finally:
            await client.close()

    @webdav_safe
    async def rm(self, paths: list[str], __event_emitter__=None) -> list[dict]:
        """Delete files or directories. Each path is attempted independently and reported per path."""
        client = self._webdav_client()
        results: list[dict] = []
        try:
            await self._ensure_sandbox(client)
            for p in paths:
                try:
                    full_path = validate_path(p, self.valves)
                    await self._check_blacklisted_recursive(client, full_path)
                    await client.clean(_webdav_path(full_path))
                    results.append({"path": p, "result": "True"})
                except Exception as e:
                    results.append(
                        {"path": p, "result": "False", "details": _sanitize(str(e))}
                    )
            return results
        finally:
            await client.close()

    async def _recursive_cp(
        self, client, src_full: str, dst_full: str, _copied: list | None = None
    ) -> None:
        """Recursively copy a file or directory.

        Args:
            _copied: internal list tracking successfully copied paths.
                     Populated on failure so the caller can see partial progress.
        """
        src_path = _webdav_path(src_full)
        dst_path = _webdav_path(dst_full)
        if _copied is None:
            _copied = []
        if await client.is_dir(src_path):
            try:
                await client.mkdir(dst_path, recursive=True)
            except Exception:
                try:
                    await client.is_dir(dst_path)
                except Exception:
                    raise
            items = await client.list_files(src_path)
            src_stripped = _strip_leading_slash(src_full)
            for item in items:
                item_stripped = _strip_leading_slash(item).rstrip("/")
                if item_stripped == src_stripped:
                    continue
                name = os.path.basename(item_stripped)
                if not name:
                    continue
                await self._recursive_cp(
                    client,
                    src_full.rstrip("/") + "/" + name,
                    dst_full.rstrip("/") + "/" + name,
                    _copied,
                )
            _copied.append(dst_full)
        else:
            await client.copy(
                remote_path_from=src_path,
                remote_path_to=dst_path,
            )
            _copied.append(dst_full)

    @staticmethod
    def _dst_inside_src(src: str, dst: str) -> bool:
        """Return True if dst equals src or is a descendant of src."""
        s = _strip_leading_slash(src).rstrip("/")
        d = _strip_leading_slash(dst).rstrip("/")
        return d == s or d.startswith(s + "/")

    @webdav_safe
    async def mv(self, src: str, dst: str, __event_emitter__=None) -> None:
        """Move or rename a file or directory (recursive for directories). Rejects moving into its own descendant."""
        src_full = validate_path(src, self.valves)
        dst_full = validate_path(dst, self.valves)

        if self._dst_inside_src(src_full, dst_full):
            raise ValueError("destination is inside or equal to source")

        client = self._webdav_client()
        try:
            await self._ensure_sandbox(client)
            await self._check_blacklisted_recursive(client, src_full)
            self._check_blacklisted(self._get_rel_path(dst_full))
            await client.move(
                remote_path_from=_webdav_path(src_full),
                remote_path_to=_webdav_path(dst_full),
                overwrite=True,
            )
        finally:
            await client.close()

    @webdav_safe
    async def cp(self, src: str, dst: str, __event_emitter__=None) -> None:
        """Copy a file or directory recursively. Safe to re-run (idempotent). Rejects copying into its own descendant."""
        src_full = validate_path(src, self.valves)
        dst_full = validate_path(dst, self.valves)

        if self._dst_inside_src(src_full, dst_full):
            raise ValueError("destination is inside or equal to source")

        client = self._webdav_client()
        copied: list[str] = []
        try:
            await self._ensure_sandbox(client)
            await self._check_blacklisted_recursive(client, src_full)
            self._check_blacklisted(self._get_rel_path(dst_full))
            await self._recursive_cp(client, src_full, dst_full, copied)
        except Exception:
            if copied:
                raise ValueError(
                    f"partial copy: {len(copied)} path(s) copied before failure"
                )
            raise
        finally:
            await client.close()

    @webdav_safe
    async def stat(self, path: str, __event_emitter__=None) -> dict:
        """Check a path: exists, isdir, size, modified, created. Missing paths return exists: False, not an error."""
        if not path:
            raise ValueError("path cannot be empty")
        full_path = validate_path(path, self.valves)
        self._check_blacklisted(self._get_rel_path(full_path))
        rel = self._get_rel_path(full_path)
        client = self._webdav_client()
        try:
            await self._ensure_sandbox(client)
            res_path = _webdav_path(full_path)
            if not await client.check(res_path):
                return {
                    "path": rel,
                    "exists": False,
                    "isdir": False,
                    "size": None,
                    "modified": None,
                    "created": None,
                }
            info = await client.info(res_path)
            return {
                "path": rel,
                "exists": True,
                "isdir": await client.is_dir(res_path),
                "size": self._format_size(info.get("size", "0")),
                "modified": self._format_datetime(info.get("modified", "")),
                "created": self._format_datetime(info.get("created", "")),
            }
        finally:
            await client.close()

    @webdav_safe
    async def tree(
        self, path: str | None = None, depth: int = 3, __event_emitter__=None
    ) -> list[str]:
        """List the directory tree as indented lines; directories end with /. depth: max levels (1-10, default 3)."""
        depth = max(1, min(int(depth), 10))
        full_path = validate_path(path if path else "", self.valves)
        self._check_blacklisted(self._get_rel_path(full_path))
        client = self._webdav_client()
        try:
            await self._ensure_sandbox(client)
            infos = await client.list_with_infos(
                _webdav_path(full_path), recursive=True
            )
            root = _strip_leading_slash(full_path).rstrip("/")
            target_rel = (
                ""
                if root == self.sandbox_prefix.rstrip("/")
                else self._get_rel_path(root)
            )
            entries: list[tuple[str, bool]] = []
            for item in infos:
                raw = _strip_leading_slash(item.get("path", ""))
                if raw.startswith(self.sandbox_prefix):
                    sandbox_rel = raw[len(self.sandbox_prefix) :]
                elif self.sandbox_prefix in raw:
                    sandbox_rel = raw.split(self.sandbox_prefix, 1)[1]
                else:
                    sandbox_rel = raw
                sandbox_rel = sandbox_rel.strip("/")
                if not sandbox_rel or self._is_result_blacklisted(sandbox_rel):
                    continue
                if target_rel:
                    if not sandbox_rel.startswith(target_rel + "/"):
                        continue
                    rel = sandbox_rel[len(target_rel) + 1 :]
                else:
                    rel = sandbox_rel
                if rel.count("/") + 1 > depth:
                    continue
                entries.append((rel, str(item.get("isdir", "False")).lower() == "true"))
            entries.sort(key=lambda e: e[0])
            return [
                "  " * rel.count("/") + os.path.basename(rel) + ("/" if is_dir else "")
                for rel, is_dir in entries
            ]
        finally:
            await client.close()

    # ============================================================
    # TASK OPERATIONS
    # ============================================================

    @caldav_safe
    async def tasks(
        self, list_name: str | None = None, __event_emitter__=None
    ) -> list[dict]:
        """Retrieve tasks from a list, nested by subtasks (roots are tasks without a parent)."""
        list_name = list_name or self.valves.DEFAULT_TASK_LIST
        if not is_whitelisted(self.valves.TASK_LIST_WHITELIST, list_name):
            raise Exception(f"{list_name!r} not whitelisted")

        client = await self._caldav_client()
        try:
            principal = await client.principal()
            cal = await self._get_calendar(principal, list_name)
            todos = await cal.todos()

            # Build flat task map.
            task_map: dict[str, dict] = {}
            for todo in todos:
                uid = str(todo.component["uid"])
                # Extract parent-only RELATED-TO from raw iCal, ignoring CHILD
                # reverse-relations added by caldav's _handle_reverse_relations.
                ical_text = todo.component.to_ical().decode()
                parent_id = None
                for m in re.finditer(
                    r"RELATED-TO(;RELTYPE=(?:PARENT|[^;]*))?:([^;\r\n]+)", ical_text
                ):
                    reltype_part = m.group(1)
                    rel_uid = m.group(2).strip()
                    if reltype_part is None or reltype_part == ";RELTYPE=PARENT":
                        parent_id = rel_uid
                        break

                task_map[uid] = {
                    key: (
                        str(todo.component.get(key))
                        if todo.component.get(key) is not None
                        else None
                    )
                    for key in [
                        "summary",
                        "description",
                        "location",
                        "url",
                        "priority",
                    ]
                }
                if parent_id is not None:
                    task_map[uid]["related-to"] = parent_id

            # Build parent->children map.
            subtasks_map: dict[str, list[str]] = {}
            for uid, task_data in task_map.items():
                parent_id = task_data.get("related-to")
                if parent_id and parent_id in task_map:
                    subtasks_map.setdefault(parent_id, []).append(uid)

            def build_subtree(task_id, _visited: set | None = None):
                if _visited is None:
                    _visited = set()
                if task_id in _visited:
                    return {"cyclic": True}
                _visited = _visited | {task_id}
                task_data = task_map.get(task_id)
                if not task_data:
                    return []
                node = {k: v for k, v in task_data.items() if k != "related-to"}
                if task_id in subtasks_map:
                    node["subtasks"] = [
                        build_subtree(child_id, _visited)
                        for child_id in subtasks_map[task_id]
                    ]
                return node

            # Collect root tasks (no parent or parent not in map).
            tree = []
            for task_id, task_data in task_map.items():
                parent_id = task_data.get("related-to")
                if not parent_id or parent_id not in task_map:
                    tree.append(build_subtree(task_id))

            return tree

        finally:
            await client.close()

    @caldav_safe
    async def add_task(
        self,
        summary: str,
        list_name: str | None = None,
        priority: int | None = 0,
        description: str | None = None,
        categories: list[str] | None = None,
        url: str | None = None,
        location: str | None = None,
        parent: str | None = None,
        __event_emitter__=None,
    ) -> str:
        """Add a task. priority: 0-9 (lower = more urgent, 0 = none). Use parent (summary or uid) to make a subtask."""
        list_name = list_name or self.valves.DEFAULT_TASK_LIST
        if not is_whitelisted(self.valves.TASK_LIST_WHITELIST, list_name):
            raise Exception(f"{list_name!r} not whitelisted")

        uid = str(uuid.uuid4())
        client = await self._caldav_client()
        try:
            principal = await client.principal()
            cal = await self._get_calendar(principal, list_name)
            kwargs = {
                "uid": uid,
                "summary": summary,
                "priority": priority,
                "description": description,
                "categories": categories,
                "url": url,
                "location": location,
            }
            if parent:
                kwargs["parent"] = [await self._resolve_task_uid(cal, parent)]
            await cal.save_todo(**kwargs)
            return summary
        finally:
            await client.close()

    @caldav_safe
    async def edit_task(
        self,
        summary: str | None = None,
        uid: str | None = None,
        new_summary: str | None = None,
        list_name: str | None = None,
        new_priority: int | None = None,
        new_location: str | None = None,
        new_description: str | None = None,
        new_url: str | None = None,
        new_categories: list[str] | None = None,
        new_related_to: str | None = None,
        __event_emitter__=None,
    ) -> None:
        """Edit a task by summary or uid. Only provided fields change. new_related_to: parent summary/uid (reparenting is cycle-safe)."""
        list_name = list_name or self.valves.DEFAULT_TASK_LIST
        if not is_whitelisted(self.valves.TASK_LIST_WHITELIST, list_name):
            raise Exception(f"{list_name!r} not whitelisted")

        if not (summary or uid):
            raise Exception("must specify summary or uid of task to edit")

        client = await self._caldav_client()
        try:
            principal = await client.principal()
            cal = await self._get_calendar(principal, list_name)
            todo = await self._find_task_by_uid_or_summary(cal, uid, summary)

            if new_summary is not None:
                todo.component["summary"] = new_summary.strip()
            if new_location is not None:
                todo.component["location"] = new_location
            if new_description is not None:
                todo.component["description"] = new_description
            if new_categories is not None:
                todo.component["categories"] = new_categories
            if new_priority is not None:
                todo.component["priority"] = max(0, min(9, new_priority))
            if new_url is not None:
                todo.component["url"] = new_url

            # Set parent with cycle detection.
            if new_related_to:
                my_uid = str(todo.component["uid"])
                parent_uid = await self._resolve_task_uid(cal, new_related_to)
                if parent_uid == my_uid:
                    raise ValueError("task cannot be its own parent")

                # Build ancestor chain to detect cycles.
                parent_map: dict[str, str] = {}
                for t in await cal.todos():
                    tid = str(t.component["uid"])
                    rel = t.component.get("related-to")
                    if rel is not None:
                        for r in [rel] if not isinstance(rel, list) else rel:
                            if r.params.get("RELTYPE") == "PARENT":
                                parent_map[tid] = str(r)

                visited = {my_uid}
                cur = parent_uid
                while cur in parent_map and cur not in visited:
                    visited.add(cur)
                    cur = parent_map[cur]
                if cur in visited:
                    raise ValueError(
                        "setting this parent would create a circular reference"
                    )
                todo.component.pop("related-to", None)
                todo.component.add(
                    "related-to", parent_uid, parameters={"RELTYPE": "PARENT"}
                )

            await todo.save()
        finally:
            await client.close()

    @caldav_safe
    async def complete_task(
        self,
        summary: str | None = None,
        uid: str | None = None,
        list_name: str | None = None,
        __user__: dict = {},
        __event_emitter__=None,
    ) -> None:
        """Mark a task as completed by summary or uid."""
        list_name = list_name or self.valves.DEFAULT_TASK_LIST
        if not is_whitelisted(self.valves.TASK_LIST_WHITELIST, list_name):
            raise Exception(f"{list_name!r} not whitelisted")

        client = await self._caldav_client()
        try:
            principal = await client.principal()
            cal = await self._get_calendar(principal, list_name)
            todo = await self._find_task_by_uid_or_summary(cal, uid, summary)
            if todo.component.get("status") == "NEEDS-ACTION":
                todo.component["status"] = "COMPLETED"
                todo.component.add(
                    "completed", datetime.now(timezone.utc).replace(microsecond=0)
                )
                todo.component["percent-complete"] = 100
            await todo.save()
        finally:
            await client.close()

    @caldav_safe
    async def delete_task(
        self,
        summary: str | None = None,
        uid: str | None = None,
        list_name: str | None = None,
        __event_emitter__=None,
    ) -> None:
        """Delete a task by summary or uid."""
        list_name = list_name or self.valves.DEFAULT_TASK_LIST
        if not is_whitelisted(self.valves.TASK_LIST_WHITELIST, list_name):
            raise Exception(f"{list_name!r} not whitelisted")

        if not (summary or uid):
            raise Exception("must specify summary or uid of task to delete")

        client = await self._caldav_client()
        try:
            principal = await client.principal()
            cal = await self._get_calendar(principal, list_name)
            todo = await self._find_task_by_uid_or_summary(cal, uid, summary)
            await todo.delete()
        finally:
            await client.close()

    # ============================================================
    # CALENDAR EVENT OPERATIONS
    # ============================================================

    @caldav_safe
    async def create_calendar_event(
        self,
        summary: str,
        calendar_name: str | None = None,
        start: str | None = None,
        end: str | None = None,
        description: str | None = None,
        location: str | None = None,
        alarms: list[str] = ["0min"],
        rrule: str | None = None,
        __user__: dict = {},
        __event_emitter__=None,
    ) -> str:
        """Create an event. start/end: ISO 8601 (naive = user's timezone; default now→now+1h).
        alarms: relative offsets like ['0min', '15min', '1h', '3d'] ('0min' = at start).
        rrule: RRULE string for recurrence, e.g. 'FREQ=WEEKLY;BYDAY=MO,WE,FR'; omit for one-off.
        """
        calendar_name = calendar_name or self.valves.DEFAULT_CALENDAR
        if not is_whitelisted(self.valves.CALENDAR_WHITELIST, calendar_name):
            raise Exception(f"{calendar_name!r} not in whitelist")

        zi = ZoneInfo(__user__["timezone"])
        now = datetime.now(zi).replace(second=0, microsecond=0)
        client = await self._caldav_client()
        try:
            principal = await client.principal()
            cal = await self._get_calendar(principal, calendar_name)

            uid = str(uuid.uuid4())
            e = Event()
            e.add("uid", uid)
            e.add("summary", summary)
            e.add("dtstamp", now)
            e.add("created", now)
            e.add("last-modified", now)

            # Start/end: default to now / now+1h; apply user timezone if naive.
            dtstart = datetime.fromisoformat(start) if start else now
            if dtstart.tzinfo is None:
                dtstart = dtstart.replace(tzinfo=zi)
            dtend = (
                datetime.fromisoformat(end) if end else dtstart + timedelta(hours=1.0)
            )
            if dtend.tzinfo is None:
                dtend = dtend.replace(tzinfo=zi)
            e.add("dtstart", dtstart)
            e.add("dtend", dtend)

            if description:
                e.add("description", description)
            if location:
                e.add("location", location)
            if rrule:
                e.add("rrule", rrule)

            # Add alarm triggers.
            if alarms:
                for r in parse_reminders(alarms):
                    a = Alarm()
                    a.add("action", "DISPLAY")
                    a.add("trigger", timedelta(minutes=-r.get("minutes")))
                    a.add("description", summary)
                    e.add_component(a)

            await cal.save_event(ical=e)
            return summary
        finally:
            await client.close()

    @caldav_safe
    async def edit_calendar_event(
        self,
        __user__: dict = {},
        summary: str | None = None,
        uid: str | None = None,
        calendar_name: str | None = None,
        new_summary: str | None = None,
        new_start: str | None = None,
        new_end: str | None = None,
        new_description: str | None = None,
        new_location: str | None = None,
        new_alarms: list[str] | None = None,
        new_rrule: str | None = None,
        __event_emitter__=None,
    ) -> None:
        """Edit an event by summary or uid. Only provided fields change.
        new_alarms: relative offsets like ['15min']. new_rrule: RRULE string, or None to remove recurrence.
        """
        calendar_name = calendar_name or self.valves.DEFAULT_CALENDAR
        if not is_whitelisted(self.valves.CALENDAR_WHITELIST, calendar_name):
            raise Exception(f"{calendar_name!r} not in whitelist")
        if not (summary or uid):
            raise Exception("must provide a summary or uid")

        zi = ZoneInfo(__user__["timezone"])
        client = await self._caldav_client()
        try:
            principal = await client.principal()
            cal = await self._get_calendar(principal, calendar_name)
            e = await self._find_event_by_uid_or_summary(cal, uid, summary)

            if new_start:
                dtstart = datetime.fromisoformat(new_start)
                if dtstart.tzinfo is None:
                    dtstart = dtstart.replace(tzinfo=zi)
                del e.component["dtstart"]
                e.component.add("dtstart", dtstart)
            if new_end:
                dtend = datetime.fromisoformat(new_end)
                if dtend.tzinfo is None:
                    dtend = dtend.replace(tzinfo=zi)
                del e.component["dtend"]
                e.component.add("dtend", dtend)
            if new_summary is not None:
                e.component["summary"] = new_summary.strip()
            if new_location is not None:
                e.component["location"] = new_location
            if new_description is not None:
                e.component["description"] = new_description

            # RRule: set to value, or remove entirely if None.
            if new_rrule is not None:
                e.component["rrule"] = new_rrule
            elif "rrule" in e.component:
                e.component.pop("rrule")

            # Replace all alarms.
            if new_alarms is not None:
                valarm_subs = [
                    sub for sub in e.component.subcomponents if sub.name == "VALARM"
                ]
                for sub in valarm_subs[:]:
                    e.component.subcomponents.remove(sub)
                for reminder in parse_reminders(new_alarms):
                    a = Alarm()
                    a.add("action", "DISPLAY")
                    a.add("trigger", timedelta(minutes=-reminder.get("minutes")))
                    a.add("description", e.component["summary"])
                    e.component.add_component(a)

            await e.save()
        finally:
            await client.close()

    @caldav_safe
    async def calendar_events(
        self,
        calendar_name: str | None = None,
        __user__: dict = {},
        __event_emitter__=None,
    ) -> list[dict]:
        """Retrieve upcoming events from a calendar (next 30 days)."""
        calendar_name = calendar_name or self.valves.DEFAULT_CALENDAR
        if not is_whitelisted(self.valves.CALENDAR_WHITELIST, calendar_name):
            raise Exception(f"{calendar_name!r} not in whitelist")

        event_data = []
        client = await self._caldav_client()
        try:
            principal = await client.principal()
            cal = await self._get_calendar(principal, calendar_name)
            tz = ZoneInfo(__user__["timezone"])
            events = await cal.search(
                start=datetime.now(tz),
                end=datetime.now(tz) + timedelta(days=30),
                expand=False,
                event=True,
            )

            for e in events:
                event_dict: dict[str, str | list[str]] = {}

                for field in ["summary", "description", "location", "organizer", "url"]:
                    if val := e.component.get(field):
                        event_dict[field] = str(val)

                if cats := e.component.get("categories"):
                    event_dict["categories"] = [str(c) for c in cats.cats]

                dtstart_val = e.component.get("dtstart")
                dtend_val = e.component.get("dtend")
                if dtstart_val:
                    event_dict["dtstart"] = dtstart_val.dt.isoformat()
                if dtend_val:
                    event_dict["dtend"] = dtend_val.dt.isoformat()

                # Handle recurring events: compute next occurrence.
                if e.component.get("rrule"):
                    rrule_str = e.component["rrule"].to_ical().decode("utf-8")
                    event_dict["rrule"] = rrule_str
                    try:
                        duration = (
                            (dtend_val.dt - dtstart_val.dt)
                            if dtstart_val and dtend_val
                            else timedelta(hours=1)
                        )
                        dtstart_dt = dtstart_val.dt
                        if isinstance(dtstart_dt, date) and not isinstance(
                            dtstart_dt, datetime
                        ):
                            dtstart_dt = datetime.combine(
                                dtstart_dt,
                                datetime.min.time(),
                                tzinfo=ZoneInfo(__user__["timezone"]),
                            )
                        elif dtstart_dt.tzinfo is None:
                            dtstart_dt = dtstart_dt.replace(
                                tzinfo=ZoneInfo(__user__["timezone"])
                            )
                        else:
                            dtstart_dt = dtstart_dt.astimezone(
                                ZoneInfo(__user__["timezone"])
                            )

                        rrule_obj = rrulestr(rrule_str, dtstart=dtstart_dt)
                        now = datetime.now(ZoneInfo(__user__["timezone"]))
                        next_occ = rrule_obj.after(now, inc=False)

                        if next_occ:
                            event_dict["dtstart"] = next_occ.isoformat()
                            event_dict["dtend"] = (next_occ + duration).isoformat()
                    except Exception:
                        pass

                if len(e.component.alarms.times) > 0:
                    event_dict["alarms"] = [
                        str(time.trigger) for time in e.component.alarms.times
                    ]

                event_data.append(event_dict)

            return event_data
        finally:
            await client.close()

    @caldav_safe
    async def delete_calendar_event(
        self,
        uid: str | None = None,
        summary: str | None = None,
        calendar_name: str | None = None,
        __event_emitter__=None,
    ) -> None:
        """Delete an event by summary or uid."""
        calendar_name = calendar_name or self.valves.DEFAULT_CALENDAR
        if not is_whitelisted(self.valves.CALENDAR_WHITELIST, calendar_name):
            raise Exception(f"{calendar_name!r} not in whitelist")
        if not (summary or uid):
            raise Exception("must provide a summary or uid")

        client = await self._caldav_client()
        try:
            principal = await client.principal()
            cal = await self._get_calendar(principal, calendar_name)
            event = await self._find_event_by_uid_or_summary(cal, uid, summary)
            await event.delete()
        finally:
            await client.close()
