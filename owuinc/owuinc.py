"""
title: owuinc
author: soakedcardinal
git_url: https://github.com/soakedcardinal/owuinc
description: Manage files, tasks, and calendars via WebDAV and CalDAV.
requirements: caldav>=3.0.0,icalendar>=6.0,aiowebdav2>=0.6,pydantic>=2,tiktoken>=0.5,aiohttp>=3.9,python-dateutil>=2.8.2
version: 3.17.1
license: MIT
"""

import asyncio
import fnmatch
import functools
import inspect
import logging
import os
import re
import time
import urllib.parse
import uuid
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from typing import Any, Callable
from zoneinfo import ZoneInfo

import tiktoken
from aiowebdav2 import Client as WebDAVClient
from aiowebdav2.client import ClientOptions
from aiowebdav2.exceptions import (
    ConnectionExceptionError,
    NoConnectionError,
    RemoteResourceNotFoundError,
    ResponseErrorCodeError,
)
from aiowebdav2.models import PropertyRequest
from caldav.aio import get_async_davclient
from caldav.lib.error import NotFoundError
from dateutil.rrule import rrule, rruleset, rrulestr
from icalendar import Alarm, Component, Event, vRecur
from pydantic import BaseModel, Field

_logger = logging.getLogger("owuinc")
if not _logger.handlers:  # loader re-execs this module on every source save
    _logger.addHandler(logging.StreamHandler())
_logger.propagate = False
_logger.setLevel(logging.DEBUG)

# tiktoken ships with OpenWebUI, so the encoder is always available here.
_tokenizer = tiktoken.get_encoding("cl100k_base")

# Per-process caches; every entry is dropped whenever any request 404s, so a
# renamed/deleted sandbox or calendar can never stay stale beyond one call.
_SANDBOX_VERIFIED: set[tuple[str, str, str]] = set()
_CALENDAR_URL_CACHE: dict[tuple[str, str], tuple[float, dict[str, str]]] = {}
_CALENDAR_CACHE_TTL_S = 60.0
_GREP_CONCURRENCY = 5
_GREP_SEARCH_TIMEOUT_S = 5.0
_GETETAG_REQ = PropertyRequest(name="getetag", namespace="DAV:")
_BINARY_EXTS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".webp",
        ".ico",
        ".tif",
        ".tiff",
        ".pdf",
        ".zip",
        ".gz",
        ".bz2",
        ".xz",
        ".tar",
        ".7z",
        ".rar",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        ".class",
        ".pyc",
        ".o",
        ".a",
        ".mp3",
        ".mp4",
        ".avi",
        ".mov",
        ".mkv",
        ".webm",
        ".ogg",
        ".wav",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".odt",
        ".ods",
        ".odp",
        ".sqlite",
        ".db",
    }
)


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


def _sanitize(msg: str, secrets: tuple[str, ...] = ()) -> str:
    """Strip URLs, UUIDs, and WebDAV paths from error messages to prevent info leaks.

    Literal credential values in `secrets` are redacted first so they never
    surface verbatim in tool output or logs (aiohttp auth errors can echo
    configured credentials).
    """
    for secret in secrets:
        if secret:
            msg = msg.replace(secret, "<redacted>")
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
        target = kwargs.get("summary", "(unknown)")
        list_name = kwargs.get("list_name") or "(default)"
        return f"edit_task '{target}' {list_name}"

    if op == "complete_task":
        target = kwargs.get("summary", "(unknown)")
        list_name = kwargs.get("list_name") or "(default)"
        return f"complete_task '{target}' {list_name}"

    if op == "delete_task":
        target = kwargs.get("summary", "(unknown)")
        list_name = kwargs.get("list_name") or "(default)"
        return f"delete_task '{target}' {list_name}"

    if op == "create_calendar_event":
        summary = kwargs.get("summary", "")
        cal = kwargs.get("calendar_name") or "(default)"
        return f"create_event '{summary}' {cal}"

    if op == "edit_calendar_event":
        target = kwargs.get("summary", "(unknown)")
        cal = kwargs.get("calendar_name") or "(default)"
        return f"edit_event '{target}' {cal}"

    if op == "calendar_events":
        cal = kwargs.get("calendar_name") or "(default)"
        cmd = f"calendar_events {cal}"
        tok = _token_line(op, data)
        return f"{cmd}\n{tok}" if tok else cmd

    if op == "delete_calendar_event":
        target = kwargs.get("summary", "(unknown)")
        cal = kwargs.get("calendar_name") or "(default)"
        return f"delete_event '{target}' {cal}"

    return f"{op}: {_format_result(response)}"


async def _emit(emitter, event: dict):
    """Emit an event through the OpenWebUI event emitter (best effort)."""
    if not emitter:
        return
    try:
        await emitter(event)
    except Exception:
        _logger.debug("event emitter failed", exc_info=True)


def _invalidate_caches(valves) -> None:
    """Drop per-process cache entries for this user's server."""
    if valves is None:
        return
    base = getattr(valves, "NEXTCLOUD_BASE_URL", "")
    _CALENDAR_URL_CACHE.pop((base, getattr(valves, "NEXTCLOUD_USERNAME", "")), None)
    sandbox = str(getattr(valves, "SANDBOX_DIR", "")).strip().rstrip("/")
    _SANDBOX_VERIFIED.discard((base, getattr(valves, "WEBDAV_USERNAME", ""), sandbox))


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

        secrets: tuple[str, ...] = ()
        if valves is not None:
            secrets = tuple(
                s
                for s in (
                    getattr(valves, "NEXTCLOUD_APP_PASSWORD", ""),
                    getattr(valves, "NEXTCLOUD_USERNAME", ""),
                )
                if s
            )

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

        t0 = time.perf_counter()

        try:
            result = func(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            response = {"result": "True"}
            if result is not None:
                response["data"] = result

            elapsed = round((time.perf_counter() - t0) * 1000)
            res_str = _format_result(response)
            if debug:
                _logger.info(f"{op}: success ({elapsed}ms) → {res_str}")
            else:
                _logger.info(f"{op}: success")

            desc = _format_status(op, kwargs, response)
            await _emit(
                emitter,
                {
                    "type": "status",
                    "data": {
                        "description": desc,
                        "done": True,
                    },
                },
            )
            return response

        except Exception as e:
            elapsed = round((time.perf_counter() - t0) * 1000)
            if isinstance(e, (RemoteResourceNotFoundError, NotFoundError)):
                _invalidate_caches(valves)
            if isinstance(e, _CONNECTION_EXC):
                details = (
                    f"connection error ({type(e).__name__})"
                    if debug
                    else "connection error"
                )
            else:
                details = _sanitize(str(e), secrets) or _sanitize(
                    type(e).__name__, secrets
                )

            if debug:
                _logger.warning(f"{op}: error ({elapsed}ms) → {details}", exc_info=True)
            else:
                _logger.warning(f"{op}: error → {details}")

            await _emit(
                emitter,
                {
                    "type": "status",
                    "data": {
                        "description": f"{op}: {details}",
                        "done": True,
                    },
                },
            )

            if isinstance(e, _CONNECTION_EXC):
                await _emit(
                    emitter,
                    {
                        "type": "notification",
                        "data": {"content": f"{op}: connection error"},
                    },
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


def _regex_line_hits(pattern: re.Pattern, content: str) -> list[tuple[int, str]]:
    return [
        (num, line)
        for num, line in enumerate(content.splitlines(), start=1)
        if pattern.search(line)
    ]


def _expand_braces(pattern: str) -> list[str]:
    """Expand brace groups in a glob: '*.{py,js}' -> ['*.py', '*.js'].

    Multiple groups per pattern work; nesting is not supported.
    """
    start = pattern.find("{")
    if start == -1:
        return [pattern]
    end = pattern.find("}", start + 1)
    if end == -1:
        return [pattern]
    prefix, suffix = pattern[:start], pattern[end + 1 :]
    out: list[str] = []
    for alt in pattern[start + 1 : end].split(","):
        out.extend(_expand_braces(prefix + alt + suffix))
    return out


def _glob_match(rel_path: str, pattern: str) -> bool:
    """Match a search-dir-relative path against a glob pattern.

    A pattern without '/' matches the basename at any depth ('*.py' finds
    everything). '**/' spans directories; '*' and '?' never cross '/'.
    """
    if "/" not in pattern:
        return fnmatch.fnmatch(os.path.basename(rel_path), pattern)
    parts: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        if pattern.startswith("**/", i):
            parts.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            parts.append(".*")
            i += 2
        elif pattern[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            parts.append("[^/]")
            i += 1
        elif pattern[i] == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                parts.append(re.escape("["))
                i += 1
            else:
                cls = pattern[i + 1 : end]
                if cls.startswith("!"):
                    cls = "^" + cls[1:]
                parts.append("[" + cls + "]")
                i = end + 1
        else:
            parts.append(re.escape(pattern[i]))
            i += 1
    return re.match("^" + "".join(parts) + "$", rel_path) is not None


def _check_redos_risk(pattern: str) -> None:
    """Raise ValueError if pattern contains nested quantifiers that can cause ReDoS."""
    try:
        from re import _parser as re_parser  # type: ignore[attr-defined]
        from re._constants import _NamedIntConstant  # type: ignore[attr-defined]
    except ImportError:
        # Private stdlib modules moved/renamed: degrade to a length cap.
        if len(pattern) > 500:
            raise ValueError("pattern too long for safe ReDoS analysis")
        return

    def _token_name(t):
        return t.name if isinstance(t, _NamedIntConstant) else str(t)

    # flags must match the caller's re.compile(pattern): parsing with
    # re.VERBOSE strips whitespace/#-comments, validating a different
    # pattern than the one that actually runs.
    parsed = list(re_parser.parse(pattern, 0))
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

    # Only actual parent-directory SEGMENTS traverse; names like "..hidden"
    # or "a..b" are legitimate filenames.
    if any(seg == ".." for seg in path.split("/")):
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
    """Parse reminder strings like '15min', '1h', '3d', '2w' into dicts."""
    if not reminders:
        return []

    parsed = []
    for r in reminders:
        r = str(r).strip().lower()
        minutes = 0
        matched = False

        if r in ("0", "0min", "0 min"):
            matched = True
        elif r.endswith(("w", "wk", "wks", "week", "weeks")):
            m = re.search(r"\d+", r)
            if m is not None:
                minutes = int(m.group()) * 10080
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


def _as_date_only(value: object) -> date | None:
    """Return the value if it's a plain date (all-day), else None."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return None


def _parse_rrule(rrule: str) -> vRecur:
    """Parse and validate an RRULE string into an icalendar vRecur value.

    Not optional: Component.add("rrule", <str>) calls vRecur(<str>), which is a
    CaselessDict and raises on a string; and a raw assignment
    (component["rrule"] = "FREQ=WEEKLY;BYDAY=MO") serializes through vText,
    which escapes the separators and writes 'FREQ=WEEKLY\\;BYDAY=MO'.
    """
    text = rrule.strip()
    if text.upper().startswith("RRULE:"):
        text = text.split(":", 1)[1].strip()
    if not text:
        raise ValueError("empty RRULE")
    try:
        parsed = vRecur.from_ical(text)
    except Exception as e:
        raise ValueError(f"invalid RRULE: {e}") from None
    if not any(k.upper() == "FREQ" for k in parsed):
        raise ValueError("RRULE must contain FREQ (e.g. 'FREQ=WEEKLY;BYDAY=MO')")
    try:
        # dateutil catches semantically broken rules that parse fine as key=value.
        rrulestr(text, dtstart=datetime.now(timezone.utc))
    except Exception as e:
        raise ValueError(f"invalid RRULE: {e}") from None
    return parsed


def _resolve_timezone(user: dict | None, default: str = "UTC") -> ZoneInfo:
    """Timezone from OpenWebUI's __user__, falling back to default.

    OpenWebUI may omit __user__ entirely or lack the "timezone" key (e.g.
    API-key access without OAuth), and __user__["timezone"] then raised
    KeyError, which surfaced as the useless error "'timezone'".
    """
    name = (user or {}).get("timezone") or default or "UTC"
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def _parse_date_loose(value: str) -> date:
    """Parse an ISO date or datetime string, returning just the date part."""
    try:
        return date.fromisoformat(value)
    except ValueError:
        return datetime.fromisoformat(value).date()


def _task_due_matches(due_prop, want: date) -> bool:
    """True if a task's DUE property falls on the given date."""
    if due_prop is None:
        return False
    dt = due_prop.dt
    return (dt.date() if isinstance(dt, datetime) else dt) == want


def _event_starts_on(dtstart_prop, want: date) -> bool:
    """True if an event's DTSTART falls on the given date."""
    if dtstart_prop is None:
        return False
    dt = dtstart_prop.dt
    return (dt.date() if isinstance(dt, datetime) else dt) == want


def _get_parent_uid(component) -> str | None:
    """Parent UID from a component's RELATED-TO properties, or None.

    RELATED-TO may occur several times (single value or list). A missing
    RELTYPE parameter means PARENT per RFC 5545; CHILD reverse-relations
    added by caldav's _handle_reverse_relations are ignored.
    """
    rel = component.get("related-to")
    if rel is None:
        return None
    for r in rel if isinstance(rel, list) else [rel]:
        if str(r.params.get("RELTYPE", "PARENT")).upper() == "PARENT":
            return str(r)
    return None


def _to_aware(value: date | datetime, tz: ZoneInfo) -> datetime:
    """Coerce an iCal date/datetime value to an aware datetime in tz."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, datetime.min.time(), tzinfo=tz)
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value


def _expand_occurrences(
    rrule_str: str,
    dtstart: datetime,
    window_start: datetime,
    window_end: datetime,
    exdates: list[datetime] | None = None,
    overrides: dict[datetime, datetime | None] | None = None,
) -> list[tuple[datetime, datetime | None]]:
    """Occurrence starts of a recurring series inside [window_start, window_end].

    Returns (start, replaced_instance) tuples sorted by start; replaced_instance
    is the original instance start when an override applied, else None.
    dtstart/window bounds must be aware datetimes. EXDATE instances are dropped;
    overrides maps RECURRENCE-ID instance starts to their new start, or None
    when the instance was cancelled. An occurrence beginning exactly at
    window_start is included.
    """
    rset = rruleset()
    rule = rrulestr(rrule_str, dtstart=dtstart)
    if not isinstance(rule, rrule):
        raise ValueError("unsupported RRULE value")
    rset.rrule(rule)
    for ex in exdates or []:
        rset.exdate(ex)
    ov = overrides or {}

    result: list[tuple[datetime, datetime | None]] = []
    seen: set[datetime] = set()
    for start in rset.between(window_start, window_end, inc=True):
        replaced = None
        if start in ov:
            new = ov[start]
            if new is None:
                continue
            start, replaced = new, start
        if start in seen:
            continue
        seen.add(start)
        result.append((start, replaced))
    for orig, new in ov.items():
        # Override moved an instance from outside the window into it.
        if new is not None and new not in seen and window_start <= new <= window_end:
            seen.add(new)
            result.append((new, orig))
    result.sort(key=lambda item: item[0])
    return result


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
        DEFAULT_TIMEZONE: str = Field(
            default="UTC",
            description="Fallback timezone when the user profile has none",
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
        READ_ONLY_PATHS: str = Field(
            default="AGENTS.md,SOUL.md,IDENTITY.md,TOOLS.md,STYLE.md,USER.md,MEMORY.md",
            description=(
                "Comma-separated paths (relative to sandbox root) the agent may read (cat/ls/grep/stat) but never modify via write/append/edit/rm/mv/cp. Defaults to the files the startup-context filter injects as system prompt, so the agent cannot rewrite its own instructions."
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

        The display-name -> URL mapping is cached briefly per (server, user)
        to avoid one PROPFIND per calendar on every operation; the cache is
        dropped whenever any CalDAV call 404s, so a renamed or deleted
        calendar can never be reused stale beyond a single failed call.

        Raises NotFoundError if no match or multiple matches found.
        """
        key = (self.valves.NEXTCLOUD_BASE_URL, self.valves.NEXTCLOUD_USERNAME)
        cached = _CALENDAR_URL_CACHE.get(key)
        if cached and cached[0] > time.monotonic():
            url = cached[1].get(calendar_name)
            if url:
                return principal.client.calendar(url=url)
        _CALENDAR_URL_CACHE.pop(key, None)
        calendars = await principal.get_calendars()
        mapping: dict[str, str] = {}
        names: list[str] = []
        matches = []
        for cal in calendars:
            display_name = await cal.get_display_name()
            if display_name:
                mapping[display_name] = str(cal.url)
                names.append(display_name)
            if display_name == calendar_name:
                matches.append(cal)
        if len(names) == len(set(names)):
            _CALENDAR_URL_CACHE[key] = (
                time.monotonic() + _CALENDAR_CACHE_TTL_S,
                mapping,
            )
        if len(matches) > 1:
            raise NotFoundError(f"multiple calendars named {calendar_name!r}")
        if len(matches) == 1:
            return matches[0]
        raise NotFoundError(f"No calendar with name {calendar_name!r} found")

    async def _resolve_task_uid(self, cal, identifier: str) -> str:
        """Resolve a parent-task identifier (by summary) to its UID.

        The model never sees a uid, so identifier must be a summary, not a
        uid: matching an identifier that happens to look like one is
        removed, since it invited a "pass the uid back" workflow the model
        cannot follow. Raises if not found or ambiguous; ambiguity is
        reported by due date, mirroring _find_task_by_summary.
        """
        todos = await cal.todos()
        norm_identifier = identifier.strip().lower()
        matches = [
            t
            for t in todos
            if norm_identifier == str(t.component["summary"]).strip().lower()
        ]
        if not matches:
            raise Exception(f"parent task with summary {identifier!r} not found")
        if len(matches) > 1:
            options = []
            for t in matches:
                due_val = t.component.get("due")
                due_s = due_val.dt.isoformat() if due_val else "no due date"
                options.append(f"due {due_s}")
            raise Exception(
                f"{len(matches)} tasks named {identifier!r} — "
                + "; ".join(options)
                + ". This parent reference is ambiguous; rename one of the "
                "tasks or use edit_task's due=/description_contains= on the "
                "child after creating it to set the parent unambiguously."
            )
        return str(matches[0].component["uid"])

    async def _find_task_by_summary(
        self,
        cal,
        summary: str,
        due: str | None = None,
        description_contains: str | None = None,
    ):
        """Find a task by summary, narrowing on due/description if ambiguous.

        Raises if not found or still ambiguous. The error names each
        candidate by due date and description snippet only — uid is never
        surfaced, because the model has no way to have obtained one.
        """
        norm_summary = summary.strip().lower()
        todos = await cal.todos()
        matches = [
            t
            for t in todos
            if norm_summary == str(t.component["summary"]).strip().lower()
        ]
        if not matches:
            raise Exception(f"no task named {summary!r} found")

        if len(matches) > 1 and due:
            want = _parse_date_loose(due)
            narrowed = [
                t for t in matches if _task_due_matches(t.component.get("due"), want)
            ]
            if narrowed:
                matches = narrowed

        if len(matches) > 1 and description_contains:
            needle = description_contains.strip().lower()
            narrowed = [
                t
                for t in matches
                if needle in str(t.component.get("description") or "").lower()
            ]
            if narrowed:
                matches = narrowed

        if len(matches) > 1:
            options = []
            for t in matches:
                due_val = t.component.get("due")
                due_s = due_val.dt.isoformat() if due_val else "no due date"
                desc = str(t.component.get("description") or "").strip()
                desc_s = f", description starting {desc[:40]!r}" if desc else ""
                options.append(f"due {due_s}{desc_s}")
            raise Exception(
                f"{len(matches)} tasks named {summary!r} — "
                + "; ".join(options)
                + ". Retry with due= and/or description_contains= to pick one."
            )
        return matches[0]

    async def _find_event_by_summary(
        self,
        cal,
        summary: str,
        on_date: str | None = None,
        description_contains: str | None = None,
    ):
        """Find an event by summary, narrowing on start date/description if
        ambiguous. Raises if not found or still ambiguous. Candidates are
        described by start time and description only — never uid.
        """
        norm_summary = summary.strip().lower()
        events = await cal.events()
        matches = [
            e
            for e in events
            if norm_summary == str(e.component["summary"]).strip().lower()
        ]
        if not matches:
            raise NotFoundError(f"no event named {summary!r} found")

        if len(matches) > 1 and on_date:
            want = _parse_date_loose(on_date)
            narrowed = [
                e for e in matches if _event_starts_on(e.component.get("dtstart"), want)
            ]
            if narrowed:
                matches = narrowed

        if len(matches) > 1 and description_contains:
            needle = description_contains.strip().lower()
            narrowed = [
                e
                for e in matches
                if needle in str(e.component.get("description") or "").lower()
            ]
            if narrowed:
                matches = narrowed

        if len(matches) > 1:
            options = []
            for e in matches:
                dtstart = e.component.get("dtstart")
                start_s = dtstart.dt.isoformat() if dtstart else "no start time"
                desc = str(e.component.get("description") or "").strip()
                desc_s = f", description starting {desc[:40]!r}" if desc else ""
                options.append(f"starts {start_s}{desc_s}")
            raise NotFoundError(
                f"{len(matches)} events named {summary!r} — "
                + "; ".join(options)
                + ". Retry with on_date= and/or description_contains= to pick one."
            )
        return matches[0]

    # -- Sandbox & blacklist helpers --

    async def _ensure_sandbox(self, client):
        """Ensure sandbox directory exists (must be called within webdav context)."""
        sandbox = self.valves.SANDBOX_DIR.strip().rstrip("/")
        if not sandbox:
            return
        key = (
            self.valves.NEXTCLOUD_BASE_URL,
            self.valves.WEBDAV_USERNAME,
            sandbox,
        )
        if key in _SANDBOX_VERIFIED:
            return
        try:
            await client.list_files(_webdav_path(sandbox + "/"))
        except RemoteResourceNotFoundError:
            await client.mkdir(_webdav_path(sandbox))
        _SANDBOX_VERIFIED.add(key)

    async def _get_etag(self, client, res_path: str) -> str | None:
        """Fetch the current ETag of a resource, or None if it has none."""
        prop = await client.get_property(res_path, _GETETAG_REQ)
        if prop is None or not prop.value:
            return None
        return str(prop.value)

    async def _conditional_put(
        self, client, res_path: str, payload: bytes, etag: str | None
    ) -> bool:
        """PUT guarded by If-Match so the server rejects stale writes (412).

        Returns False when the ETag no longer matches (someone else wrote
        the file since we read it), True on success. Nextcloud's WebDAV
        locker grants every LOCK without enforcing it, so LOCK is not real
        conflict protection; If-Match is enforced by both Nextcloud and
        standard WebDAV servers.
        """
        headers = {"If-Match": etag} if etag else {}
        try:
            await client.execute_request(
                "upload", res_path, data=payload, headers_ext=headers
            )
        except ResponseErrorCodeError as e:
            if e.code == 412:
                return False
            raise
        return True

    def _check_blacklisted(self, rel_path: str) -> None:
        """Raise ValueError if rel_path (relative to sandbox) is blacklisted."""
        rel_path = rel_path.strip("/")
        if rel_path and is_blacklisted(self.valves.FILE_BLACKLIST, rel_path):
            raise ValueError("Access denied")

    async def _check_blacklisted_recursive(self, client, full_path: str) -> None:
        """Raise ValueError if full_path or any descendant is blacklisted.

        Fail-closed: if the descendant listing cannot be retrieved the
        operation is denied — a transient server error must not let a delete
        or move proceed unchecked.
        """
        rel_path = self._get_rel_path(full_path).strip("/")
        self._check_blacklisted(rel_path)
        if not self.valves.FILE_BLACKLIST:
            return
        root = _strip_leading_slash(full_path).rstrip("/")
        try:
            infos = await client.list_with_infos(
                _webdav_path(full_path), recursive=True
            )
        except RemoteResourceNotFoundError:
            raise
        except Exception:
            raise ValueError("Access denied")
        for info in infos:
            item_path = _strip_leading_slash(str(info.get("path", ""))).rstrip("/")
            if not item_path or item_path == root:
                continue
            if self._is_result_blacklisted(self._get_rel_path(item_path)):
                raise ValueError("Access denied")

    def _is_result_blacklisted(self, rel_path: str) -> bool:
        """Check if a result path (relative to sandbox) should be hidden."""
        rel_path = rel_path.strip("/")
        return bool(rel_path) and is_blacklisted(self.valves.FILE_BLACKLIST, rel_path)

    def _check_not_sandbox_root(self, full_path: str) -> None:
        """Reject operations that target the sandbox root itself (rm/mv)."""
        target = _strip_leading_slash(full_path).strip("/")
        root = _strip_leading_slash(self.sandbox_prefix).strip("/")
        if target == root:
            raise ValueError("operation on sandbox root is not allowed")

    def _check_read_only(self, rel_path: str) -> None:
        """Raise ValueError if rel_path is on the read-only protection list."""
        rel_path = rel_path.strip("/")
        if rel_path and is_blacklisted(self.valves.READ_ONLY_PATHS, rel_path):
            raise ValueError("path is read-only")

    def _secrets(self) -> tuple[str, ...]:
        return tuple(
            s
            for s in (
                self.valves.NEXTCLOUD_APP_PASSWORD,
                self.valves.NEXTCLOUD_USERNAME,
            )
            if s
        )

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
                if not cal_name or not is_whitelisted(
                    self.valves.CALENDAR_WHITELIST, cal_name
                ):
                    continue
                components = [c.upper() for c in await cal.get_supported_components()]
                if components and "VEVENT" not in components:
                    continue  # VTODO-only collection belongs to task_lists()
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
                if not tl or not is_whitelisted(self.valves.TASK_LIST_WHITELIST, tl):
                    continue
                components = [c.upper() for c in await cal.get_supported_components()]
                if components and "VTODO" not in components:
                    continue  # VEVENT-only collection belongs to calendars()
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
        """Find files by glob pattern, always recursive. Patterns match paths relative to the search dir: '*.py' matches anywhere, 'docs/*.md' is depth-exact, 'docs/**/*.md' any depth under docs. Brace expansion ok: '*.{py,js}'."""
        target_dir = validate_path(path if path else "", self.valves)
        rel_path = self._get_rel_path(target_dir)
        self._check_blacklisted(rel_path)
        if len(pattern) > 200:
            raise ValueError("pattern too long")

        client = self._webdav_client()
        try:
            await self._ensure_sandbox(client)

            all_files = await client.list_with_infos(
                _webdav_path(target_dir), recursive=True
            )
            files_only = [
                f for f in all_files if str(f.get("isdir", "False")).lower() != "true"
            ]

            patterns_to_match = _expand_braces(pattern)

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

                if any(_glob_match(rel_to_target, pat) for pat in patterns_to_match):
                    matched.append(
                        {
                            "path": full_path,
                            "modified": file_info.get("modified", ""),
                        }
                    )

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
        Text files only — binary files are skipped and listed in the result. Nested quantifiers (e.g. '(a+)+') are rejected; a pattern that executes too long aborts the search.
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
            sem = asyncio.Semaphore(_GREP_CONCURRENCY)

            async def search_one(full_path: str) -> None:
                if self.sandbox_prefix in full_path:
                    rel = full_path.split(self.sandbox_prefix, 1)[1]
                elif full_path.startswith(search_rel + "/"):
                    rel = full_path[len(search_rel) + 1 :]
                else:
                    rel = os.path.basename(full_path)

                if self._is_result_blacklisted(rel):
                    return

                filename = os.path.basename(rel)

                if patterns_to_match:
                    matched = False
                    for pat in patterns_to_match:
                        pattern_name = pat.split("/")[-1] if "/" in pat else pat
                        if fnmatch.fnmatch(filename, pattern_name):
                            matched = True
                            break
                    if not matched:
                        return

                if os.path.splitext(filename)[1].lower() in _BINARY_EXTS:
                    skipped.append(rel)
                    return

                buf = BytesIO()
                async with sem:
                    try:
                        # The listing returns full hrefs (rooted at
                        # /remote.php/dav/files/<user>/), so fetch the listed
                        # path itself instead of re-deriving it from rel.
                        root_prefix = (
                            f"remote.php/dav/files/{self.valves.WEBDAV_USERNAME}/"
                        )
                        if full_path.startswith(root_prefix):
                            fetch_path = "/" + full_path[len(root_prefix) :]
                        else:
                            fetch_path = _webdav_path(validate_path(rel, self.valves))
                        await client.resource(fetch_path).read_from(buf)
                    except Exception:
                        return

                try:
                    content = buf.getvalue().decode("utf-8")
                except UnicodeDecodeError:
                    skipped.append(rel)
                    return

                try:
                    hits = await asyncio.wait_for(
                        asyncio.to_thread(_regex_line_hits, compiled_regex, content),
                        timeout=_GREP_SEARCH_TIMEOUT_S,
                    )
                except TimeoutError:
                    raise ValueError(
                        f"regex execution timed out on {rel} after "
                        f"{int(_GREP_SEARCH_TIMEOUT_S)}s; search aborted"
                    )
                for line_num, line in hits:
                    results.append(
                        {
                            "file": rel,
                            "line": line_num,
                            "content": line.strip(),
                        }
                    )

            outcomes = await asyncio.gather(
                *(search_one(fp) for fp in file_list), return_exceptions=True
            )
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    raise outcome

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
        self._check_read_only(self._get_rel_path(full_path))
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
        Uses optimistic concurrency (ETag + If-Match) to prevent concurrent
        read-modify-write conflicts; retries once if the file changed mid-write.
        """
        if content is None:
            content = ""
        full_path = validate_path(path, self.valves)
        self._check_blacklisted(self._get_rel_path(full_path))
        self._check_read_only(self._get_rel_path(full_path))
        client = self._webdav_client()
        try:
            await self._ensure_sandbox(client)
            res_path = _webdav_path(full_path)
            for attempt in range(2):
                try:
                    etag = await self._get_etag(client, res_path)
                except RemoteResourceNotFoundError:
                    etag = None
                if etag is None:
                    await client.resource(res_path).write_to(
                        BytesIO(content.encode("utf-8"))
                    )
                    return
                buf = BytesIO()
                await client.resource(res_path).read_from(buf)
                try:
                    existing = buf.getvalue().decode("utf-8")
                except UnicodeDecodeError:
                    raise ValueError("not a text file")
                payload = existing + content
                if existing and not existing.endswith("\n"):
                    payload = existing + "\n" + content
                if await self._conditional_put(
                    client, res_path, payload.encode("utf-8"), etag
                ):
                    return
                if attempt == 1:
                    raise ValueError(
                        "file kept changing during append; concurrent writer conflict"
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
        Uses optimistic concurrency (ETag + If-Match) to prevent concurrent
        read-modify-write conflicts; retries once if the file changed mid-write.
        """
        if not old_string:
            raise ValueError("old_string cannot be empty")
        if old_string == new_string:
            raise ValueError("old_string and new_string must be different")

        full_path = validate_path(file_path, self.valves)
        self._check_blacklisted(self._get_rel_path(full_path))
        self._check_read_only(self._get_rel_path(full_path))
        client = self._webdav_client()
        try:
            await self._ensure_sandbox(client)
            res_path = _webdav_path(full_path)
            for attempt in range(2):
                try:
                    etag = await self._get_etag(client, res_path)
                except RemoteResourceNotFoundError:
                    etag = None
                if etag is None:
                    raise ValueError("file not found")
                buf = BytesIO()
                await client.resource(res_path).read_from(buf)
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
                if await self._conditional_put(
                    client, res_path, modified.encode("utf-8"), etag
                ):
                    return
                if attempt == 1:
                    raise ValueError(
                        "file kept changing during edit; concurrent writer conflict"
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
                    self._check_not_sandbox_root(full_path)
                    self._check_read_only(self._get_rel_path(full_path))
                    await self._check_blacklisted_recursive(client, full_path)
                    await client.clean(_webdav_path(full_path))
                    results.append({"path": p, "result": "True"})
                except Exception as e:
                    results.append(
                        {
                            "path": p,
                            "result": "False",
                            "details": _sanitize(str(e), self._secrets()),
                        }
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

        self._check_not_sandbox_root(src_full)
        self._check_not_sandbox_root(dst_full)
        self._check_read_only(self._get_rel_path(src_full))
        self._check_read_only(self._get_rel_path(dst_full))

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

        self._check_read_only(self._get_rel_path(dst_full))

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
                    "size_bytes": None,
                    "modified": None,
                    "created": None,
                }
            info = await client.info(res_path)
            raw_size = info.get("size")
            try:
                size_bytes: int | None = int(raw_size)
            except (TypeError, ValueError):
                size_bytes = None
            return {
                "path": rel,
                "exists": True,
                "isdir": await client.is_dir(res_path),
                "size": self._format_size(info.get("size", "0")),
                "size_bytes": size_bytes,
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
                parent_id = _get_parent_uid(todo.component)

                def _task_val(comp, key: str):
                    v = comp.get(key)
                    if v is None:
                        return None
                    if key == "due":
                        return v.dt.isoformat() if hasattr(v, "dt") else str(v)
                    return str(v)

                task_map[uid] = {
                    key: _task_val(todo.component, key)
                    for key in [
                        "summary",
                        "description",
                        "location",
                        "url",
                        "priority",
                        "status",
                        "percent-complete",
                        "due",
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
                    return {"uid": task_id, "missing": True}
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
        due: str | None = None,
        start: str | None = None,
        __user__: dict = {},
        __event_emitter__=None,
    ) -> str:
        """Add a task. priority: 0-9 (lower = more urgent, 0 = none). Use parent (summary or uid) to make a subtask.
        due/start: ISO 8601 date or datetime (naive = user's timezone).
        """
        list_name = list_name or self.valves.DEFAULT_TASK_LIST
        if not is_whitelisted(self.valves.TASK_LIST_WHITELIST, list_name):
            raise Exception(f"{list_name!r} not whitelisted")

        zi = _resolve_timezone(__user__, self.valves.DEFAULT_TIMEZONE)

        def _iso_to_dt(value: str, label: str) -> datetime:
            try:
                dt = datetime.fromisoformat(value)
            except (TypeError, ValueError):
                raise ValueError(f"{label} must be an ISO 8601 date or datetime")
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=zi)
            return dt

        uid = str(uuid.uuid4())
        client = await self._caldav_client()
        try:
            principal = await client.principal()
            cal = await self._get_calendar(principal, list_name)
            kwargs = {
                "uid": uid,
                "summary": summary,
                "priority": max(0, min(9, int(priority or 0))),
                "description": description,
                "categories": categories,
                "url": url,
                "location": location,
            }
            if due:
                kwargs["due"] = _iso_to_dt(due, "due")
            if start:
                kwargs["dtstart"] = _iso_to_dt(start, "start")
            if parent:
                kwargs["parent"] = [await self._resolve_task_uid(cal, parent)]
            await cal.save_todo(**kwargs)
            return summary
        finally:
            await client.close()

    @caldav_safe
    async def edit_task(
        self,
        summary: str,
        due: str | None = None,
        description_contains: str | None = None,
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
        """Edit a task by summary. Only provided fields change.
        If more than one task shares that summary, pass due (its due date)
        and/or description_contains (a substring of its description) — the
        error message lists the actual candidates to choose from.
        new_related_to: parent task's summary (reparenting is cycle-safe)."""
        list_name = list_name or self.valves.DEFAULT_TASK_LIST
        if not is_whitelisted(self.valves.TASK_LIST_WHITELIST, list_name):
            raise Exception(f"{list_name!r} not whitelisted")

        client = await self._caldav_client()
        try:
            principal = await client.principal()
            cal = await self._get_calendar(principal, list_name)
            todo = await self._find_task_by_summary(
                cal, summary, due=due, description_contains=description_contains
            )

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
                    rel_parent = _get_parent_uid(t.component)
                    if rel_parent is not None:
                        parent_map[tid] = rel_parent

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
        summary: str,
        due: str | None = None,
        description_contains: str | None = None,
        list_name: str | None = None,
        entire_series: bool = False,
        __user__: dict = {},
        __event_emitter__=None,
    ) -> str:
        """Mark a task as completed by summary. Safe to repeat.
        If more than one task shares that summary, pass due and/or
        description_contains to disambiguate (see the error message).
        Recurring tasks complete one occurrence at a time; pass
        entire_series=True to end the whole series instead."""
        list_name = list_name or self.valves.DEFAULT_TASK_LIST
        if not is_whitelisted(self.valves.TASK_LIST_WHITELIST, list_name):
            raise Exception(f"{list_name!r} not whitelisted")

        client = await self._caldav_client()
        try:
            principal = await client.principal()
            cal = await self._get_calendar(principal, list_name)
            todo = await self._find_task_by_summary(
                cal, summary, due=due, description_contains=description_contains
            )
            comp = todo.component
            label = str(comp.get("summary") or summary)

            # STATUS is optional in RFC 5545 and caldav's save_todo() doesn't
            # emit it, so an absent STATUS means "still open". The old
            # `== "NEEDS-ACTION"` gate therefore skipped exactly the tasks this
            # tool creates: it saved an unchanged todo and reported success.
            if str(comp.get("status") or "").upper() == "COMPLETED":
                return f"{label} (already completed)"

            now = datetime.now(timezone.utc).replace(microsecond=0)

            # Marking a recurring master COMPLETED ends the entire series,
            # because STATUS applies to the series. Default to completing just
            # the current occurrence: caldav files an independent completed
            # copy and advances the master to its next occurrence. If the
            # series is exhausted (COUNT down to one, UNTIL in the past) or
            # malformed, fall through to a plain completion.
            if "RRULE" in comp and not entire_series:
                try:
                    result = todo.complete(
                        completion_timestamp=now,
                        handle_rrule=True,
                        rrule_mode="safe",
                    )
                    if inspect.isawaitable(result):
                        await result
                    return f"{label} (occurrence completed; series continues)"
                except Exception:
                    _logger.debug("recurring completion fell back to plain completion")

            if "RRULE" in comp:
                comp.pop("rrule", None)  # a completed series must not recur
            for key, value in (
                ("status", "COMPLETED"),
                ("percent-complete", 100),
                ("completed", now),
                ("last-modified", now),
            ):
                comp.pop(key, None)  # add() appends, so drop any existing value
                comp.add(key, value)

            await todo.save()
            if entire_series:
                return f"{label} (series ended)"
            return label
        finally:
            await client.close()

    @caldav_safe
    async def delete_task(
        self,
        summary: str,
        due: str | None = None,
        description_contains: str | None = None,
        list_name: str | None = None,
        __event_emitter__=None,
    ) -> None:
        """Delete a task by summary. If more than one task shares that
        summary, pass due and/or description_contains to disambiguate."""
        list_name = list_name or self.valves.DEFAULT_TASK_LIST
        if not is_whitelisted(self.valves.TASK_LIST_WHITELIST, list_name):
            raise Exception(f"{list_name!r} not whitelisted")

        client = await self._caldav_client()
        try:
            principal = await client.principal()
            cal = await self._get_calendar(principal, list_name)
            todo = await self._find_task_by_summary(
                cal, summary, due=due, description_contains=description_contains
            )
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
        alarms: list[str] | None = None,
        rrule: str | None = None,
        __user__: dict = {},
        __event_emitter__=None,
    ) -> str:
        """Create an event. start/end: ISO 8601 (naive = user's timezone; default now→now+1h).
        A date-only start like '2026-09-20' creates an all-day event; end must then also be date-only (inclusive, e.g. '2026-09-22' spans 3 days).
        alarms: relative offsets like ['0min', '15min', '1h', '3d', '2w'] ('0min' = at start; default at-start alarm).
        rrule: RRULE string for recurrence, e.g. 'FREQ=WEEKLY;BYDAY=MO,WE,FR'; omit for one-off.
        """
        if alarms is None:
            alarms = ["0min"]
        calendar_name = calendar_name or self.valves.DEFAULT_CALENDAR
        if not is_whitelisted(self.valves.CALENDAR_WHITELIST, calendar_name):
            raise Exception(f"{calendar_name!r} not in whitelist")

        parsed_rrule = _parse_rrule(rrule) if rrule else None

        zi = _resolve_timezone(__user__, self.valves.DEFAULT_TIMEZONE)
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

            # Start/end: default now/now+1h; apply user timezone if naive.
            # Date-only values create all-day events (DTSTART;VALUE=DATE).
            date_only = r"\d{4}-\d{2}-\d{2}"
            start_s = start.strip() if start else ""
            end_s = end.strip() if end else ""
            if start_s and re.fullmatch(date_only, start_s):
                if end_s and not re.fullmatch(date_only, end_s):
                    raise ValueError(
                        "all-day start (date-only) requires a date-only end"
                    )
                day_start = date.fromisoformat(start_s)
                day_end = (
                    date.fromisoformat(end_s)
                    if end_s
                    else day_start + timedelta(days=1)
                )
                if day_end < day_start:
                    raise ValueError("end must not be before start")
                e.add("dtstart", day_start)
                # DTEND is exclusive for all-day events; inclusive user intent.
                e.add("dtend", day_end + timedelta(days=1))
            else:
                dtstart = datetime.fromisoformat(start) if start else now
                if dtstart.tzinfo is None:
                    dtstart = dtstart.replace(tzinfo=zi)
                dtend = (
                    datetime.fromisoformat(end)
                    if end
                    else dtstart + timedelta(hours=1.0)
                )
                if dtend.tzinfo is None:
                    dtend = dtend.replace(tzinfo=zi)
                if dtend <= dtstart:
                    raise ValueError("end must be after start")
                e.add("dtstart", dtstart)
                e.add("dtend", dtend)

            if description:
                e.add("description", description)
            if location:
                e.add("location", location)
            if parsed_rrule is not None:
                e.add("rrule", parsed_rrule)

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
        summary: str,
        __user__: dict = {},
        on_date: str | None = None,
        description_contains: str | None = None,
        calendar_name: str | None = None,
        new_summary: str | None = None,
        new_start: str | None = None,
        new_end: str | None = None,
        new_description: str | None = None,
        new_location: str | None = None,
        new_alarms: list[str] | None = None,
        new_rrule: str | None = None,
        remove_rrule: bool = False,
        __event_emitter__=None,
    ) -> None:
        """Edit an event by summary. Only provided fields change.
        If more than one event shares that summary, pass on_date (its start
        date) and/or description_contains to disambiguate.
        Recurrence is kept as-is unless you pass new_rrule (replace it) or remove_rrule=True (make it one-off).
        Edits apply to the whole series for recurring events.
        new_start/new_end: ISO 8601 (naive = user's timezone); date-only values make it all-day (end inclusive) and both must be date-only or both datetime.
        Passing only new_start moves the event and shifts the end to keep its length; passing both validates the range.
        new_alarms: relative offsets like ['15min']; replaces all existing alarms.
        """
        calendar_name = calendar_name or self.valves.DEFAULT_CALENDAR
        if not is_whitelisted(self.valves.CALENDAR_WHITELIST, calendar_name):
            raise Exception(f"{calendar_name!r} not in whitelist")
        if remove_rrule and new_rrule:
            raise ValueError("pass either new_rrule or remove_rrule, not both")

        parsed_rrule = _parse_rrule(new_rrule) if new_rrule else None

        zi = _resolve_timezone(__user__, self.valves.DEFAULT_TIMEZONE)
        client = await self._caldav_client()
        try:
            principal = await client.principal()
            cal = await self._get_calendar(principal, calendar_name)
            e = await self._find_event_by_summary(
                cal, summary, on_date=on_date, description_contains=description_contains
            )

            start_s = new_start.strip() if new_start else ""
            end_s = new_end.strip() if new_end else ""
            if start_s or end_s:
                date_only = r"\d{4}-\d{2}-\d{2}"

                def _parse_new(value: str) -> date | datetime:
                    if re.fullmatch(date_only, value):
                        return date.fromisoformat(value)
                    dt = datetime.fromisoformat(value)
                    return dt.replace(tzinfo=zi) if dt.tzinfo is None else dt

                new_sv: date | datetime | None = (
                    _parse_new(start_s) if start_s else None
                )
                new_ev: date | datetime | None = _parse_new(end_s) if end_s else None

                def _prop_dt(name: str) -> Any:
                    # component.get() returns a vDDDTypes wrapper; .dt is
                    # the native date/datetime.
                    val = e.component.get(name)
                    return val.dt if hasattr(val, "dt") else val

                old_s = _prop_dt("dtstart")
                old_e = _prop_dt("dtend")
                eff_s = new_sv if new_sv is not None else old_s
                eff_e = new_ev if new_ev is not None else old_e
                s_day = _as_date_only(eff_s)
                e_day = _as_date_only(eff_e)
                if eff_e is not None and (s_day is None) != (e_day is None):
                    raise ValueError(
                        "start and end must both be date-only or both datetime"
                    )

                if s_day is not None:
                    # All-day: DTEND is exclusive, user-facing end is inclusive.
                    old_day = _as_date_only(old_s)
                    if isinstance(new_ev, date):
                        day_end: Any = new_ev + timedelta(days=1)
                    else:
                        day_end = old_e
                        if day_end is not None and old_day is not None:
                            # Start-only move: shift the end to keep the
                            # event's length instead of failing.
                            day_end = day_end + (s_day - old_day)
                    if day_end is not None and day_end <= s_day:
                        raise ValueError("end must not be before start")
                    e.component.pop("dtstart", None)
                    e.component.pop("dtend", None)
                    e.component.pop("duration", None)
                    e.component.add("dtstart", s_day)
                    if day_end is not None:
                        e.component.add("dtend", day_end)
                else:
                    if not isinstance(eff_s, datetime):
                        raise ValueError(
                            "new_start must be an ISO 8601 date or datetime"
                        )
                    s_dt = eff_s if eff_s.tzinfo else eff_s.replace(tzinfo=zi)
                    e_dt: datetime | None = None
                    shifted = False
                    if eff_e is not None:
                        if not isinstance(eff_e, datetime):
                            raise ValueError(
                                "new_end must be an ISO 8601 date or datetime"
                            )
                        e_dt = eff_e if eff_e.tzinfo else eff_e.replace(tzinfo=zi)
                        if new_ev is None and new_sv is not None:
                            old_dt = old_s if isinstance(old_s, datetime) else None
                            if old_dt is not None:
                                old_aware = (
                                    old_dt
                                    if old_dt.tzinfo
                                    else old_dt.replace(tzinfo=zi)
                                )
                                e_dt = e_dt + (s_dt - old_aware)
                                shifted = True
                        if e_dt <= s_dt:
                            raise ValueError("end must be after start")
                    if start_s:
                        e.component.pop("dtstart", None)
                        e.component.add("dtstart", s_dt)
                    if (end_s or shifted) and e_dt is not None:
                        e.component.pop("dtend", None)
                        e.component.pop("duration", None)
                        e.component.add("dtend", e_dt)
            if new_summary is not None:
                e.component["summary"] = new_summary.strip()
            if new_location is not None:
                e.component["location"] = new_location
            if new_description is not None:
                e.component["description"] = new_description

            # Recurrence is only touched on explicit request. The old code
            # popped RRULE whenever new_rrule was omitted, so editing the
            # location of a weekly meeting quietly turned it into a one-off.
            if remove_rrule:
                e.component.pop("rrule", None)
            elif parsed_rrule is not None:
                e.component.pop("rrule", None)
                e.component.add("rrule", parsed_rrule)

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

            # Bump SEQUENCE/LAST-MODIFIED so sync clients notice the change.
            seq = int(e.component.get("sequence", 0) or 0)
            e.component.pop("sequence", None)
            e.component.add("sequence", seq + 1)
            e.component.pop("last-modified", None)
            e.component.add("last-modified", datetime.now(timezone.utc))

            await e.save()
        finally:
            await client.close()

    @caldav_safe
    async def calendar_events(
        self,
        calendar_name: str | None = None,
        start: str | None = None,
        days: int = 30,
        __user__: dict = {},
        __event_emitter__=None,
    ) -> list[dict]:
        """Retrieve events from a calendar (next 30 days by default).
        start: ISO 8601 date/datetime to open the window earlier (naive = user's timezone); days: window length (1-365).

        Recurring events expand into one entry per occurrence within the
        window; EXDATE exclusions and RECURRENCE-ID overrides are applied.
        """
        calendar_name = calendar_name or self.valves.DEFAULT_CALENDAR
        if not is_whitelisted(self.valves.CALENDAR_WHITELIST, calendar_name):
            raise Exception(f"{calendar_name!r} not in whitelist")
        days = max(1, min(int(days), 365))

        event_data = []
        client = await self._caldav_client()
        try:
            principal = await client.principal()
            cal = await self._get_calendar(principal, calendar_name)
            tz = _resolve_timezone(__user__, self.valves.DEFAULT_TIMEZONE)
            if start:
                try:
                    window_start: datetime = datetime.fromisoformat(start)
                except (TypeError, ValueError):
                    raise ValueError("start must be an ISO 8601 date or datetime")
                if window_start.tzinfo is None:
                    window_start = window_start.replace(tzinfo=tz)
            else:
                window_start = datetime.now(tz)
            window_end = window_start + timedelta(days=days)
            events = await cal.search(
                start=window_start,
                end=window_end,
                expand=False,
                event=True,
            )
            if not events:
                # Some servers (e.g. Radicale) mishandle time-range REPORTs
                # and return nothing; refetch everything and let the
                # client-side window checks below filter.
                events = await cal.events()

            # Group components by uid so RECURRENCE-ID overrides render
            # together with their master (servers return overrides as
            # separate VEVENT components).
            by_uid: dict[str, list] = {}
            for e in events:
                by_uid.setdefault(str(e.component.get("uid") or ""), []).append(e)

            for e in events:
                comp = e.component
                if comp.get("recurrence-id") is not None:
                    continue  # rendered via its master series below

                event_dict: dict[str, str | list[str]] = {}

                for field in ["summary", "description", "location", "organizer", "url"]:
                    if val := comp.get(field):
                        event_dict[field] = str(val)

                if cats := comp.get("categories"):
                    event_dict["categories"] = [str(c) for c in cats.cats]

                if len(comp.alarms.times) > 0:
                    event_dict["alarms"] = [
                        str(time.trigger) for time in comp.alarms.times
                    ]

                dtstart_val = comp.get("dtstart")
                dtend_val = comp.get("dtend")

                if comp.get("rrule") is None:
                    # One-off: keep only if it overlaps the window, even when
                    # the server ignored the time-range filter.
                    if dtstart_val:
                        ev_start = _to_aware(dtstart_val.dt, tz)
                        ev_end = _to_aware(dtend_val.dt, tz) if dtend_val else ev_start
                        if ev_end < window_start or ev_start > window_end:
                            continue
                        event_dict["dtstart"] = dtstart_val.dt.isoformat()
                    if dtend_val:
                        event_dict["dtend"] = dtend_val.dt.isoformat()
                    event_data.append(event_dict)
                    continue

                # Recurring: expand into every occurrence inside the window,
                # honoring EXDATE exclusions and RECURRENCE-ID overrides.
                # (expand=True is server-side and unsupported by many
                # servers, e.g. Radicale, so the expansion happens here.)
                if dtstart_val is None:
                    continue  # RRULE without DTSTART is malformed; skip
                rrule_value = comp["rrule"].to_ical().decode("utf-8")
                duration = (
                    dtend_val.dt - dtstart_val.dt if dtend_val else timedelta(hours=1)
                )
                master_start = _to_aware(dtstart_val.dt, tz)

                exdates: list[datetime] = []
                ex = comp.get("exdate")
                if ex is not None:
                    for x in ex if isinstance(ex, list) else [ex]:
                        # vDDDLists wraps one or more vDDDTypes values
                        dts = getattr(x, "dts", None)
                        for d in dts if dts is not None else [x]:
                            exdates.append(_to_aware(d.dt, tz))

                overrides: dict[datetime, datetime | None] = {}
                override_comps: dict[datetime, Component] = {}
                for sib in by_uid.get(str(comp.get("uid") or ""), []):
                    sc = sib.component
                    rid = sc.get("recurrence-id")
                    if rid is None:
                        continue
                    orig = _to_aware(rid.dt, tz)
                    if str(sc.get("status") or "").upper() == "CANCELLED":
                        overrides[orig] = None
                    elif sc.get("dtstart") is not None:
                        overrides[orig] = _to_aware(sc["dtstart"].dt, tz)
                    override_comps[orig] = sc

                for occ_start, replaced in _expand_occurrences(
                    rrule_value,
                    master_start,
                    window_start,
                    window_end,
                    exdates=exdates,
                    overrides=overrides,
                ):
                    occ = dict(event_dict)
                    occ["rrule"] = rrule_value
                    end = occ_start + duration
                    if (
                        replaced is not None
                        and (oc := override_comps.get(replaced)) is not None
                    ):
                        for field in [
                            "summary",
                            "description",
                            "location",
                            "organizer",
                            "url",
                        ]:
                            if val := oc.get(field):
                                occ[field] = str(val)
                        if oc.get("dtend") is not None:
                            end = _to_aware(oc["dtend"].dt, tz)
                    occ["dtstart"] = occ_start.isoformat()
                    occ["dtend"] = end.isoformat()
                    event_data.append(occ)

            return event_data
        finally:
            await client.close()

    @caldav_safe
    async def delete_calendar_event(
        self,
        summary: str,
        on_date: str | None = None,
        description_contains: str | None = None,
        calendar_name: str | None = None,
        __event_emitter__=None,
    ) -> None:
        """Delete an event by summary. If more than one event shares that
        summary, pass on_date and/or description_contains to disambiguate."""
        calendar_name = calendar_name or self.valves.DEFAULT_CALENDAR
        if not is_whitelisted(self.valves.CALENDAR_WHITELIST, calendar_name):
            raise Exception(f"{calendar_name!r} not in whitelist")

        client = await self._caldav_client()
        try:
            principal = await client.principal()
            cal = await self._get_calendar(principal, calendar_name)
            event = await self._find_event_by_summary(
                cal, summary, on_date=on_date, description_contains=description_contains
            )
            await event.delete()
        finally:
            await client.close()
