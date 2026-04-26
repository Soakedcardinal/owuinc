"""
title: owuinc
author: soakedcardinal
git_url: https://github.com/soakedcardinal/owuinc
description: Manage files, tasks, and calendars via WebDAV and CalDAV.
requirements: caldav>=3.0.0,icalendar,aiowebdav2
version: 3.0.0
license: MIT
"""

import fnmatch
import functools
import inspect
import os
import re
import traceback
import urllib.parse
import uuid
from datetime import date, datetime, timedelta
from io import BytesIO
from typing import Any, Callable, List, Optional
from zoneinfo import ZoneInfo

from aiowebdav2 import Client as WebDAVClient
from aiowebdav2.exceptions import (
    ConnectionExceptionError,
    LocalResourceNotFoundError,
    NoConnectionError,
    RemoteParentNotFoundError,
    RemoteResourceNotFoundError,
    ResourceLockedError,
    WebDavError,
)
from caldav.aio import get_async_davclient
from caldav.lib.error import NotFoundError
from dateutil.rrule import rrulestr
from icalendar import Alarm, Event
from pydantic import BaseModel, Field

DEBUG = False
# DEBUG = True


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


def log_valves(valves):
    if DEBUG:
        print(f"NEXTCLOUD_BASE_URL={valves.NEXTCLOUD_BASE_URL!r}")
        print(f"WEBDAV_USERNAME={valves.WEBDAV_USERNAME!r}")
        print(f"NEXTCLOUD_USERNAME={valves.NEXTCLOUD_USERNAME!r}")
        print(f"NEXTCLOUD_APP_PASSWORD_LEN={len(valves.NEXTCLOUD_APP_PASSWORD)}")
        print(f"SANDBOX_DIR={valves.SANDBOX_DIR!r}")
        print(f"DEFAULT_CALENDAR={valves.DEFAULT_CALENDAR!r}")
        print(f"DEFAULT_TASK_LIST={valves.DEFAULT_TASK_LIST!r}")
        print(f"CALENDAR_WHITELIST={valves.CALENDAR_WHITELIST!r}")
        print(f"TASK_LIST_WHITELIST={valves.TASK_LIST_WHITELIST!r}")


def tool_logger(func: Callable) -> Callable:
    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        log_sep(func.__name__)
        log_valves(self.valves)
        result = func(self, *args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    return wrapper


def caldav_safe(func: Callable) -> Callable:
    @functools.wraps(func)
    async def wrapper(*args, **kwargs) -> dict:
        op = func.__name__
        try:
            result = func(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            response = {"result": "True"}
            if result is not None:
                response["data"] = result
            return response
        except NotFoundError as e:
            log(f"{op}: {e}")
            msg = e.args[0] if hasattr(e, "args") and e.args else str(e)
            return {"result": "False", "details": msg}
        except Exception as e:
            log_err(
                f"{op}: unexpected error - {type(e).__name__}: {e}\
                \nTraceback: {traceback.format_exc()}"
            )
            return {"result": "False", "details": f"{op}: error ({type(e).__name__})"}

    return wrapper


def webdav_safe(func: Callable) -> Callable:
    @functools.wraps(func)
    async def wrapper(*args, **kwargs) -> dict:
        op = func.__name__
        try:
            result = func(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            response = {"result": "True"}
            if result is not None:
                response["data"] = result
            return response
        except (RemoteResourceNotFoundError, RemoteParentNotFoundError) as e:
            log_err(
                f"{op}: resource not found - {e}\nTraceback: {traceback.format_exc()}"
            )
            return {"result": "False", "details": f"{op}: not found"}
        except LocalResourceNotFoundError as e:
            log_err(
                f"{op}: local file not found - {e}\nTraceback: {traceback.format_exc()}"
            )
            return {"result": "False", "details": f"{op}: local file not found"}
        except ResourceLockedError as e:
            log_err(f"{op}: resource locked - {e}\nTraceback: {traceback.format_exc()}")
            return {"result": "False", "details": f"{op}: resource locked"}
        except (ConnectionExceptionError, NoConnectionError) as e:
            log_err(
                f"{op}: connection failed - {e}\nTraceback: {traceback.format_exc()}"
            )
            return {"result": "False", "details": f"{op}: connection failed"}
        except WebDavError as e:
            log_err(f"{op}: WebDAV error - {e}\nTraceback: {traceback.format_exc()}")
            return {"result": "False", "details": f"{op}: {str(e)}"}
        except ValueError as e:
            log_err(f"{op}: validation error - {e}")
            return {"result": "False", "details": f"{op}: {str(e)}"}
        except Exception as e:
            log_err(
                f"{op}: unexpected error - {type(e).__name__}: {e}\
                \nTraceback: {traceback.format_exc()}"
            )
            return {"result": "False", "details": f"{op}: error ({type(e).__name__})"}

    return wrapper


def _webdav_path(p: str) -> str:
    """Ensure path has leading / for aiowebdav2."""
    return p if p.startswith("/") else "/" + p


def _strip_leading_slash(p: str) -> str:
    """Strip leading / from aiowebdav2 paths to match webdavclient3 format."""
    return p.lstrip("/") if p else p


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


def parse_reminders(reminders: list | None = None) -> list:
    if not reminders:
        return []
    parsed = []
    for r in reminders:
        minutes = 0
        if r in ["0", "0min", "0 min"]:
            minutes = 0
        elif r.endswith("min") or r.endswith("mins") or r.endswith("minutes"):
            match = re.search(r"\d+", r)
            if match is not None:
                minutes = int(match.group())
        elif (
            r.endswith("h")
            or r.endswith("hr")
            or r.endswith("hour")
            or r.endswith("hours")
        ):
            match = re.search(r"\d+", r)
            if match is not None:
                hours = int(match.group())
                minutes = hours * 60
        elif r.endswith("d") or r.endswith("day") or r.endswith("days"):
            match = re.search(r"\d+", r)
            if match is not None:
                days = int(match.group())
                minutes = days * 1440
        parsed.append({"minutes": minutes, "action": "DISPLAY"})
    return parsed


def is_whitelisted(whitelist: str, item: str) -> bool:
    """Check if item is in whitelist"""
    if not whitelist:
        log_err("whitelist is empty")
        return False
    cleaned_whitelist = {s.strip() for s in whitelist.split(",") if s.strip()}
    return item in cleaned_whitelist


class Tools:
    def __init__(self):
        log_sep("Tools")
        self.valves = self.Valves()

    class Valves(BaseModel):
        NEXTCLOUD_BASE_URL: str = Field("", description="Nextcloud server address")
        WEBDAV_USERNAME: str = Field("")
        NEXTCLOUD_USERNAME: str = Field("")
        NEXTCLOUD_APP_PASSWORD: str = Field("", json_schema_extra={"secret": True})
        SANDBOX_DIR: str = Field(
            default="owuinc",
            description=(
                "Directory for all file operations. Leading `/` is optional"
                " and will be stripped. Directory is auto-created if missing."
                " Leave empty to use Nextcloud root."
            ),
        )
        DEFAULT_CALENDAR: str = Field(
            default="Personal", description="Default calendar for event operations"
        )
        DEFAULT_TASK_LIST: str = Field(
            default="Tasks", description="Default task list for task operations"
        )
        CALENDAR_WHITELIST: str = Field(
            default="Personal", description="Comma-separated list of allowed calendars"
        )
        TASK_LIST_WHITELIST: str = Field(
            default="Tasks",
            description="Comma-separated list of allowed task lists",
        )
        pass  # required for parsing

    @property
    def webdav_client(self):
        base = self.valves.NEXTCLOUD_BASE_URL
        wd_user = self.valves.WEBDAV_USERNAME
        url = f"{base}/remote.php/dav/files/{wd_user}/"
        log(f"create webdav_client with url={url!r}")
        try:
            return WebDAVClient(
                url,
                self.valves.NEXTCLOUD_USERNAME,
                self.valves.NEXTCLOUD_APP_PASSWORD,
            )
        except Exception as e:
            log_err(f"failed to create webdav_client: {type(e).__name__}: {e}")
            raise

    @property
    def caldav_client(self):
        base = self.valves.NEXTCLOUD_BASE_URL
        url = f"{base}/remote.php/dav"
        log(f"creating new caldav_client with url={url!r}")
        try:
            return get_async_davclient(
                username=self.valves.NEXTCLOUD_USERNAME,
                password=self.valves.NEXTCLOUD_APP_PASSWORD,
                url=url,
                features="nextcloud",
                enable_rfc6764=False,
            )
        except Exception as e:
            log_err(
                f"Failed to create caldav_client: \
                    {type(e).__name__}: {e}"
            )
            raise

    async def _get_calendar(self, principal, calendar_name: str):
        """Get a calendar by name, working around caldav.aio's broken calendar().

        caldav.aio.CalendarSet.calendar(name=...) calls get_calendars() and
        get_display_name() internally without awaiting them, which fails for
        async clients. This helper properly awaits all async calls.
        """
        calendars = await principal.get_calendars()
        for cal in calendars:
            display_name = await cal.get_display_name()
            if display_name == calendar_name:
                return cal
        raise NotFoundError(f"No calendar with name {calendar_name!r} found")

    async def _ensure_sandbox(self, client):
        """Ensure sandbox directory exists.

        Must be called within a webdav client context.
        """
        sandbox = self.valves.SANDBOX_DIR.strip().rstrip("/")
        if sandbox:
            try:
                log(f"checking sandbox dir exists: {sandbox!r}")
                await client.list_files(_webdav_path(sandbox + "/"))
            except RemoteResourceNotFoundError:
                log(f"sandbox dir not found, creating: {sandbox!r}")
                await client.mkdir(_webdav_path(sandbox))

    @tool_logger
    @caldav_safe
    async def get_calendars(self) -> list[str]:
        """Retrieve available calendars"""
        client = await self.caldav_client
        principal = await client.principal()
        calendars = await principal.get_calendars()
        available = [await c.get_display_name() for c in calendars]
        log(f"found {len(available)} calendars: {available!r}")

        return [
            cal_name
            for cal_name in available
            if is_whitelisted(self.valves.CALENDAR_WHITELIST, cal_name)
        ]

    @tool_logger
    @caldav_safe
    async def get_task_lists(self) -> list[str]:
        """Retrieve available task lists"""
        client = await self.caldav_client
        principal = await client.principal()
        calendars = await principal.get_calendars()
        available = [await c.get_display_name() for c in calendars]
        log(f"found {len(available)} task lists: {available!r}")

        return [
            tl
            for tl in available
            if is_whitelisted(self.valves.TASK_LIST_WHITELIST, tl)
        ]

    @tool_logger
    @webdav_safe
    async def mkdir(
        self,
        path: str,
    ) -> None:
        """Create new directory"""
        client = self.webdav_client
        try:
            await self._ensure_sandbox(client)
            await client.mkdir(_webdav_path(validate_path(path, self.valves)))
        finally:
            await client.close()

    @tool_logger
    @webdav_safe
    async def ls(self, path: str | None = None) -> list[str]:
        """List files and directories"""
        client = self.webdav_client
        try:
            await self._ensure_sandbox(client)
            p = validate_path(path, self.valves)
            prefix = f"{self.valves.SANDBOX_DIR.strip().rstrip('/')}/"
            raw_paths = await client.list_files(_webdav_path(p))
            paths = [_strip_leading_slash(rp) for rp in raw_paths]
            parent = p.strip(prefix).strip("/")
            result_list = [
                item for item in paths if item != prefix and item.strip("/") != parent
            ]
            return result_list
        finally:
            await client.close()

    @tool_logger
    @webdav_safe
    async def glob(
        self,
        pattern: str,
        path: Optional[str] = None,
    ) -> list[str]:
        """File pattern matching tool

        Use this tool when you need to find files by name patterns

        Args:
            pattern: The glob pattern to match files against
                (e.g., "**/*.py", "src/**/*.tsx")
            path: The directory to search in.

        Returns:
            matching file paths sorted by modification time (newest last)
        """
        client = self.webdav_client
        try:
            await self._ensure_sandbox(client)
            target_dir = validate_path(path if path else "", self.valves)

            log(f"glob: pattern={pattern}, target_dir={target_dir}")

            pattern_parts = pattern.split("/")
            is_recursive = "**" in pattern_parts or (
                len(pattern_parts) == 1 and "*" in pattern_parts[0]
            )

            log(f"glob: is_recursive={is_recursive}")

            all_files = await client.list_with_infos(
                _webdav_path(target_dir), recursive=is_recursive
            )

            log(f"glob: fetched {len(all_files)} items")

            files_only = [
                f for f in all_files if str(f.get("isdir", "False")).lower() != "true"
            ]

            log(f"glob: {len(files_only)} files after filtering dirs")

            patterns_to_match = [pattern]
            if "{" in pattern and "}" in pattern:
                start = pattern.find("{")
                end = pattern.find("}", start)
                if end != -1:
                    prefix, suffix = pattern[:start], pattern[end + 1 :]
                    alternatives = pattern[start + 1 : end].split(",")
                    patterns_to_match = [prefix + alt + suffix for alt in alternatives]

            matched = []
            for file_info in files_only:
                full_path = _strip_leading_slash(file_info.get("path", ""))
                filename = os.path.basename(full_path)

                log(f"glob: checking {filename} against patterns")

                for pat in patterns_to_match:
                    pattern_name = pat.split("/")[-1] if "/" in pat else pat
                    if fnmatch.fnmatch(filename, pattern_name):
                        log(f"glob: matched {filename}")
                        matched.append(
                            {
                                "path": full_path,
                                "modified": file_info.get("modified", ""),
                            }
                        )
                        break

            try:
                matched.sort(key=lambda x: x.get("modified", ""))
            except Exception as e:
                log(f"glob: sort error - {e}")

            sandbox_prefix = self.valves.SANDBOX_DIR.strip().rstrip("/") + "/"
            result = []
            for f in matched:
                full_path = f["path"]
                if sandbox_prefix in full_path:
                    rel_path = full_path.split(sandbox_prefix, 1)[1]
                else:
                    rel_path = os.path.basename(full_path)
                result.append(rel_path)

            log(f"glob: returning {len(result)} matches")
            return result
        finally:
            await client.close()

    @tool_logger
    @webdav_safe
    async def grep(
        self,
        pattern: str,
        path: Optional[str] = None,
        include: Optional[str] = None,
    ) -> list[dict]:
        """Search file contents for pattern using regex

        Args:
            pattern: The regex pattern to search for
            path: The directory to search in. Defaults to root.
            include: File pattern to include (e.g., "*.py", "*.{ts,tsx}")
        """
        client = self.webdav_client
        try:
            await self._ensure_sandbox(client)
            target_dir = validate_path(path if path else "", self.valves)

            log(
                f"grep: pattern={pattern!r}, "
                f"target_dir={target_dir}, include={include!r}"
            )

            all_items = await client.list_with_infos(
                _webdav_path(target_dir), recursive=True
            )
            if not all_items:
                return []

            log(f"grep: fetched {len(all_items)} items")

            file_list = [
                _strip_leading_slash(item.get("path", item))
                for item in all_items
                if str(item.get("isdir", "False")).lower() != "true"
            ]

            log(f"grep: {len(file_list)} files after filtering directories")

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

            results = []
            for full_path in file_list:
                sandbox_prefix = self.valves.SANDBOX_DIR.strip().rstrip("/") + "/"
                rel_path = (
                    full_path.split(sandbox_prefix, 1)[1]
                    if sandbox_prefix in full_path
                    else os.path.basename(full_path)
                )
                filename = os.path.basename(rel_path)

                if patterns_to_match:
                    matched = False
                    for pat in patterns_to_match:
                        pattern_name = pat.split("/")[-1] if "/" in pat else pat
                        if fnmatch.fnmatch(filename, pattern_name):
                            matched = True
                            break
                    if not matched:
                        continue

                webdav_path = _webdav_path(validate_path(rel_path, self.valves))
                log(f"grep: searching {rel_path}")

                try:
                    buf = BytesIO()
                    await client.resource(webdav_path).read_from(buf)
                    content = buf.getvalue().decode("utf-8")

                    log(
                        f"grep: read {len(c := content)} bytes, "
                        f"{len(c.splitlines())} lines"
                    )

                    for line_num, line in enumerate(content.splitlines(), start=1):
                        if compiled_regex.search(line):
                            results.append(
                                {
                                    "file": rel_path,
                                    "line": line_num,
                                    "content": line.strip(),
                                }
                            )

                except Exception as e:
                    log(f"grep: error reading {rel_path}: {e}")
                    continue

            results.sort(key=lambda x: (x["file"], x["line"]))
            log(f"grep: found {len(results)} matches")
            return results
        finally:
            await client.close()

    @tool_logger
    @webdav_safe
    async def write_file(
        self,
        path: str,
        content: Optional[str] = None,
    ) -> None:
        """Write to a file, overwriting existing content"""
        if content is None:
            content = ""
        client = self.webdav_client
        try:
            await self._ensure_sandbox(client)
            await client.resource(
                _webdav_path(validate_path(path, self.valves))
            ).write_to(BytesIO(content.encode("utf-8")))
        finally:
            await client.close()

    @tool_logger
    @webdav_safe
    async def read(
        self,
        path: str,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> str:
        """Read a file with optional line range

        Args:
            path: File path to read
            offset: Line number to start from (1-indexed)
            limit: Maximum number of lines to return
        """
        if not path:
            raise ValueError("path cannot be empty")

        if offset is not None and offset < 1:
            raise ValueError(f"offset must be >= 1, got {offset}")

        if limit is not None and limit <= 0:
            raise ValueError(f"limit must be > 0, got {limit}")

        client = self.webdav_client
        try:
            await self._ensure_sandbox(client)
            buf = BytesIO()
            await client.resource(
                _webdav_path(validate_path(path, self.valves))
            ).read_from(buf)
            content = buf.getvalue().decode("utf-8")
            lines = content.splitlines()

            if offset is not None:
                start = max(0, offset - 1)
                lines = lines[start:]

            if limit is not None:
                lines = lines[:limit]

            return "\n".join(lines)
        finally:
            await client.close()

    @tool_logger
    @webdav_safe
    async def append_file(
        self,
        path: str,
        content: Optional[str] = None,
    ) -> None:
        """Append content to file, creating it if it does not exist"""
        if content is None:
            content = ""
        client = self.webdav_client
        try:
            await self._ensure_sandbox(client)
            res_path = _webdav_path(validate_path(path, self.valves))
            res = client.resource(res_path)
            try:
                buf = BytesIO()
                await res.read_from(buf)
                existing = buf.getvalue().decode("utf-8")
            except RemoteResourceNotFoundError:
                existing = ""
            await res.write_to(BytesIO((existing + content).encode("utf-8")))
        finally:
            await client.close()

    @tool_logger
    @webdav_safe
    async def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> dict:
        """Perform exact string replacement in a file.

        Replaces first match only by default.

        Args:
            file_path: Path to file
            old_string: Text to find (unique or use replace_all)
            new_string: Replacement text
            replace_all: Replace all occurrences (default: false)
        """
        if not old_string:
            raise ValueError("old_string cannot be empty")

        if old_string == new_string:
            raise ValueError("old_string and new_string must be different")

        client = self.webdav_client
        try:
            await self._ensure_sandbox(client)
            res_path = _webdav_path(validate_path(file_path, self.valves))
            buf = BytesIO()
            await client.resource(res_path).read_from(buf)
            content = buf.getvalue().decode("utf-8")

            count = content.count(old_string)

            if count == 0:
                raise ValueError("String not found")

            if count > 1 and not replace_all:
                raise ValueError(f"Found {count} matches, but replace_all is false")

            replacement_count = 1 if not replace_all else -1
            modified_content = content.replace(
                old_string, new_string, replacement_count
            )

            await client.resource(res_path).write_to(
                BytesIO(modified_content.encode("utf-8"))
            )

            return {"result": "True"}
        finally:
            await client.close()

    @tool_logger
    @webdav_safe
    async def rm(self, paths: list[str]) -> None:
        """Deletes files/directories"""
        client = self.webdav_client
        try:
            await self._ensure_sandbox(client)
            for p in paths:
                await client.clean(_webdav_path(validate_path(p, self.valves)))
        finally:
            await client.close()

    @tool_logger
    @webdav_safe
    async def mv(
        self,
        src: str,
        dst: str,
    ) -> None:
        """Move/rename a file or directory"""
        client = self.webdav_client
        try:
            await self._ensure_sandbox(client)
            await client.move(
                remote_path_from=_webdav_path(validate_path(src, self.valves)),
                remote_path_to=_webdav_path(validate_path(dst, self.valves)),
            )
        finally:
            await client.close()

    @tool_logger
    @webdav_safe
    async def cp(
        self,
        src: str,
        dst: str,
    ) -> None:
        """Copy a file or directory."""
        client = self.webdav_client
        try:
            await self._ensure_sandbox(client)
            await client.copy(
                remote_path_from=_webdav_path(validate_path(src, self.valves)),
                remote_path_to=_webdav_path(validate_path(dst, self.valves)),
            )
        finally:
            await client.close()

    @tool_logger
    @caldav_safe
    async def get_tasks(self, list_name: str | None = None) -> list[dict] | Any:
        """Retrieve task from specified list"""
        list_name = list_name or self.valves.DEFAULT_TASK_LIST
        if not is_whitelisted(self.valves.TASK_LIST_WHITELIST, list_name):
            raise Exception(f"{list_name!r} not whitelisted")

        client = await self.caldav_client
        principal = await client.principal()
        cal = await self._get_calendar(principal, list_name)
        todos = await cal.todos()

        task_map: dict[str, dict] = {}
        for todo in todos:
            task_map[todo.component["uid"]] = {
                key: todo.component.get(key)
                for key in [
                    "uid",
                    "summary",
                    "description",
                    "location",
                    "url",
                    "priority",
                    "related-to",
                ]
                if todo.component.get(key) is not None
            }
        subtasks_map: dict[str, list[str]] = {}
        for uid, task_data in task_map.items():
            parent_id = task_data.get("related-to")
            if parent_id and parent_id in task_map:
                if parent_id not in subtasks_map:
                    subtasks_map[parent_id] = []
                subtasks_map[parent_id].append(uid)

        def build_subtree(task_id):
            task_data = task_map.get(task_id)
            if not task_data:
                return []
            node = {k: v for k, v in task_data.items() if k != "related-to"}
            if task_id in subtasks_map:
                node["subtasks"] = [
                    build_subtree(child_id) for child_id in subtasks_map[task_id]
                ]
            return node

        tree = []
        for task_id, task_data in task_map.items():
            parent_id = task_data.get("related-to")
            if not parent_id or parent_id not in task_map:
                tree.append(build_subtree(task_id))
        return tree

    @tool_logger
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
    ):
        """Update task properties by summary or uid"""
        list_name = list_name or self.valves.DEFAULT_TASK_LIST
        if not is_whitelisted(self.valves.TASK_LIST_WHITELIST, list_name):
            raise Exception(f"{list_name!r} not whitelisted")

        if not (summary or uid):
            raise Exception("must specify summary or uid of task to edit")
        client = await self.caldav_client
        principal = await client.principal()
        cal = await self._get_calendar(principal, list_name)
        todo = None
        if uid:
            todo = await cal.todo_by_uid(uid)
        elif summary is not None:
            matches = []
            todos = await cal.todos()
            for todo in todos:
                if summary.strip() in todo.component["summary"]:
                    matches.append(todo.component["uid"])
            if len(matches) > 1:
                raise Exception(f"Multiple matches for {summary!r}: {matches}")
            elif len(matches) == 1:
                todo = await cal.todo_by_uid(matches[0])
        if not todo:
            raise Exception("task not found")
        if new_summary:
            todo.component["summary"] = new_summary.strip()
        if new_location:
            todo.component["location"] = new_location
        if new_description:
            todo.component["description"] = new_description
        if new_categories:
            todo.component["categories"] = new_categories
        if new_priority:
            todo.component["priority"] = max(0, min(9, new_priority))
        if new_url:
            todo.component["url"] = new_url
        if new_related_to:
            todo.component["related-to"] = new_related_to
        await todo.save()

    @tool_logger
    @caldav_safe
    async def delete_task(
        self,
        summary: Optional[str] = None,
        uid: Optional[str] = None,
        list_name: str | None = None,
    ):
        """Delete task from specified list by summary or uid"""
        list_name = list_name or self.valves.DEFAULT_TASK_LIST
        if not is_whitelisted(self.valves.TASK_LIST_WHITELIST, list_name):
            raise Exception(f"{list_name!r} not whitelisted")

        if not (summary or uid):
            raise Exception("must specify summary or uid of task to edit")
        client = await self.caldav_client
        principal = await client.principal()
        cal = await self._get_calendar(principal, list_name)
        todo = None
        if uid:
            todo = await cal.todo_by_uid(uid)
        elif summary is not None:
            matches = []
            todos = await cal.todos()
            for todo in todos:
                if summary.strip() in todo.component["summary"]:
                    matches.append(todo.component["uid"])
            if len(matches) > 1:
                raise Exception(f"Error: Multiple matches for {summary!r}: {matches}")
            elif len(matches) == 1:
                todo = await cal.todo_by_uid(matches[0])
        if not todo:
            raise Exception("Error: task not found")
        await todo.delete()

    @tool_logger
    @caldav_safe
    async def create_calendar_event(
        self,
        summary: str,
        calendar_name: str | None = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        description: Optional[str] = None,
        location: Optional[str] = None,
        alarms: List[str] = ["0min"],
        rrule: Optional[str] = None,
        __user__: dict = {},
    ):
        """Add event to specified calendar."""
        calendar_name = calendar_name or self.valves.DEFAULT_CALENDAR
        if not is_whitelisted(self.valves.CALENDAR_WHITELIST, calendar_name):
            raise Exception(f"{calendar_name!r} not in whitelist")

        zi = ZoneInfo(__user__["timezone"])
        now = datetime.now(zi).replace(second=0, microsecond=0)
        client = await self.caldav_client
        principal = await client.principal()
        cal = await self._get_calendar(principal, calendar_name)

        uid = str(uuid.uuid4())
        e = Event()
        e.add("uid", uid)
        e.add("summary", summary)
        e.add("dtstamp", now)
        e.add("created", now)
        e.add("last-modified", now)

        if start:
            dtstart = datetime.fromisoformat(start)
            if dtstart.tzinfo is None:
                dtstart = dtstart.replace(tzinfo=zi)
        else:
            dtstart = now
        if end:
            dtend = datetime.fromisoformat(end)
            if dtend.tzinfo is None:
                dtend = dtend.replace(tzinfo=zi)
        else:
            dtend = dtstart + timedelta(hours=1.0)
        e.add("dtstart", dtstart)
        e.add("dtend", dtend)

        if description:
            e.add("description", description)
        if location:
            e.add("location", location)
        if rrule:
            e.add("rrule", rrule)

        if alarms:
            for r in parse_reminders(alarms):
                a = Alarm()
                a.add("action", "DISPLAY")
                a.add("trigger", timedelta(minutes=-r.get("minutes")))
                a.add("description", summary)
                e.add_component(a)
        await cal.save_event(ical=e)
        return uid

    @tool_logger
    @caldav_safe
    async def add_task(
        self,
        summary: str,
        list_name: str | None = None,
        priority: Optional[int] = 0,
        description: Optional[str] = None,
        categories: Optional[List[str]] = None,
        url: Optional[str] = None,
        location: Optional[str] = None,
    ):
        """Add a task to the specified list."""
        list_name = list_name or self.valves.DEFAULT_TASK_LIST
        if not is_whitelisted(self.valves.TASK_LIST_WHITELIST, list_name):
            raise Exception(f"{list_name!r} not whitelisted")

        uid = str(uuid.uuid4())
        client = await self.caldav_client
        principal = await client.principal()
        calendars = await principal.get_calendars()
        available_lists = [await c.get_display_name() for c in calendars]
        if list_name not in available_lists:
            log_err("invalid task list")
            raise Exception("invalid task list")
        cal = await self._get_calendar(principal, list_name)
        await cal.save_todo(
            uid=uid,
            summary=summary,
            priority=priority,
            description=description,
            categories=categories,
            url=url,
            location=location,
        )
        return uid

    # TODO this duplicates edit task should be a wrapper
    @tool_logger
    @caldav_safe
    async def complete_task(
        self,
        summary: Optional[str] = None,
        uid: Optional[str] = None,
        list_name: str | None = None,
    ):
        """Marks a task completed."""
        list_name = list_name or self.valves.DEFAULT_TASK_LIST
        if not is_whitelisted(self.valves.TASK_LIST_WHITELIST, list_name):
            raise Exception(f"{list_name!r} not whitelisted")

        client = await self.caldav_client
        principal = await client.principal()
        cal = await self._get_calendar(principal, list_name)
        if uid:
            todo = await cal.todo_by_uid(uid)
        elif summary is not None:
            matches = []
            todos = await cal.todos()
            for t in todos:
                if summary.strip() in t.component["summary"]:
                    matches.append(t)
            if len(matches) > 1:
                raise Exception(f"multiple matches found for {summary!r}")
            if len(matches) == 0:
                raise Exception(
                    f"Task with summary {summary!r} not found in list {list_name!r}"
                )
            todo = matches[0]
        todo.component["status"] = "COMPLETED"
        await todo.save()

    @tool_logger
    @caldav_safe
    async def edit_calendar_event(
        self,
        __user__: dict = {},
        summary: Optional[str] = None,
        uid: Optional[str] = None,
        calendar_name: str | None = None,
        new_summary: Optional[str] = None,
        new_start: Optional[str] = None,
        new_end: Optional[str] = None,
        new_description: Optional[str] = None,
        new_location: Optional[str] = None,
        new_alarms: Optional[List[str]] = None,
        new_rrule: Optional[str] = None,
    ):
        """Edits events by summary/uid."""
        calendar_name = calendar_name or self.valves.DEFAULT_CALENDAR
        if not is_whitelisted(self.valves.CALENDAR_WHITELIST, calendar_name):
            raise Exception(f"{calendar_name!r} not in whitelist")

        if not (summary or uid):
            raise Exception("Error: must provide a summary or uid")

        tz = __user__["timezone"]
        zi = ZoneInfo(tz)
        client = await self.caldav_client
        principal = await client.principal()
        cal = await self._get_calendar(principal, calendar_name)

        e = None
        if uid:
            e = await cal.event_by_uid(uid)
        elif summary is not None:
            matches = []
            events = await cal.events()
            for e in events:
                if summary.strip() in e.component["summary"]:
                    matches.append(e.component["uid"])
            if len(matches) > 1:
                raise Exception("Error: multiple matches")
            elif len(matches) == 1:
                e = await cal.event_by_uid(matches[0])
        if not e:
            raise Exception("Error: event not found")

        if new_start:
            dtstart = datetime.fromisoformat(new_start)
            if dtstart.tzinfo is None:
                dtstart = dtstart.replace(tzinfo=zi)
            e.component["dtstart"].dt = dtstart
        if new_end:
            dtend = datetime.fromisoformat(new_end)
            if dtend.tzinfo is None:
                dtend = dtend.replace(tzinfo=zi)
            e.component["dtend"].dt = dtend
        if new_summary:
            e.component["summary"] = new_summary.strip()
        if new_location:
            e.component["location"] = new_location
        if new_description:
            e.component["description"] = new_description
        if new_rrule:
            e.component["rrule"] = new_rrule
        if new_alarms:
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

    @tool_logger
    @caldav_safe
    async def get_calendar_events(
        self,
        calendar_name: str | None = None,
        __user__: dict = {},
    ):
        """Retrieves upcoming events on the specified calendar."""
        calendar_name = calendar_name or self.valves.DEFAULT_CALENDAR
        if not is_whitelisted(self.valves.CALENDAR_WHITELIST, calendar_name):
            raise Exception(f"{calendar_name!r} not in whitelist")

        event_data = []
        client = await self.caldav_client
        principal = await client.principal()
        cal = await self._get_calendar(principal, calendar_name)
        events = await cal.search(
            start=datetime.now(ZoneInfo(__user__["timezone"])),
            expand=False,
            event=True,
        )

        for e in events:
            event_dict = {}

            for field in [
                "uid",
                "summary",
                "description",
                "location",
                "categories",
                "organizer",
                "url",
            ]:
                if val := e.component.get(field):
                    event_dict[field] = val

            dtstart_val = e.component.get("dtstart")
            dtend_val = e.component.get("dtend")
            if dtstart_val:
                event_dict["dtstart"] = dtstart_val.dt.isoformat()
            if dtend_val:
                event_dict["dtend"] = dtend_val.dt.isoformat()

            if e.component.get("rrule"):
                event_dict["rrule"] = e.component["rrule"].to_ical().decode("utf-8")
                log(f"Processing recurring event: {event_dict.get('summary')}")
                log(f"Original dtstart: {event_dict.get('dtstart')}")
                try:
                    duration = (
                        (dtend_val.dt - dtstart_val.dt)
                        if dtstart_val and dtend_val
                        else timedelta(hours=1)
                    )
                    log(f"Duration: {duration}")

                    dtstart_dt = dtstart_val.dt
                    if isinstance(dtstart_dt, date) and not isinstance(
                        dtstart_dt, datetime
                    ):
                        log("All-day event detected")
                        dtstart_dt = datetime.combine(
                            dtstart_dt,
                            datetime.min.time(),
                            tzinfo=ZoneInfo(__user__["timezone"]),
                        )
                    elif dtstart_dt.tzinfo is None:
                        log("Naive datetime detected")
                        dtstart_dt = dtstart_dt.replace(
                            tzinfo=ZoneInfo(__user__["timezone"])
                        )
                    else:
                        log("TZ-aware datetime detected")
                        dtstart_dt = dtstart_dt.astimezone(
                            ZoneInfo(__user__["timezone"])
                        )

                    log(f"RRULE: {event_dict['rrule']}")
                    rrule_obj = rrulestr(event_dict["rrule"], dtstart=dtstart_dt)
                    now = datetime.now(ZoneInfo(__user__["timezone"]))
                    log(f"Now: {now}")
                    next_occurrence = rrule_obj.after(now, inc=False)

                    if next_occurrence:
                        log(f"Next occurrence: {next_occurrence.isoformat()}")
                        log(
                            f"New dtstart: {event_dict['dtstart']} -> {next_occurrence}"
                        )
                        event_dict["dtstart"] = next_occurrence.isoformat()
                        event_dict["dtend"] = (next_occurrence + duration).isoformat()
                        log(f"New dtend: {event_dict['dtend']}")
                    else:
                        log("No future occurrence found")
                except Exception as err:
                    log(f"RRULE parsing failed: {err}")
                    pass

            if len(e.component.alarms.times) > 0:
                event_dict["alarms"] = [
                    str(time.trigger) for time in e.component.alarms.times
                ]

            event_data.append(event_dict)

        return event_data

    @tool_logger
    @caldav_safe
    async def delete_calendar_event(
        self,
        uid: Optional[str] = None,
        summary: Optional[str] = None,
        calendar_name: str | None = None,
    ):
        """Deletes an event from the specified calendar."""
        calendar_name = calendar_name or self.valves.DEFAULT_CALENDAR
        if not is_whitelisted(self.valves.CALENDAR_WHITELIST, calendar_name):
            raise Exception(f"{calendar_name!r} not in whitelist")

        if not (summary or uid):
            raise Exception("must provide a summary or uid")

        client = await self.caldav_client
        principal = await client.principal()
        cal = await self._get_calendar(principal, calendar_name)
        event = None
        if uid:
            event = await cal.event_by_uid(uid)
        elif summary is not None:
            matches = []
            events = await cal.events()
            for e in events:
                c = e.component
                if summary.strip() in c["summary"]:
                    matches.append(c["uid"])
            if len(matches) > 1:
                raise Exception(f"multiple matches for summary {summary!r}")
            elif len(matches) == 1:
                event = await cal.event_by_uid(matches[0])
        if not event:
            raise NotFoundError(f"event not found for {summary!r}")
        await event.delete()
