"""
title: owuinc
author: Duncan Nicholson
git_url: https://github.com/soakedcardinal/owuinc
description: file, task, and calendar management
requirements: caldav,icalendar,webdavclient3
version: 1.0.3
license: MIT
"""

import functools
import logging
import os
import re
import traceback
import urllib.parse
import uuid
from datetime import datetime, timedelta
from io import BytesIO
from typing import Any, Callable, List, Optional
from zoneinfo import ZoneInfo

from caldav.davclient import get_davclient
from caldav.lib.error import NotFoundError
from icalendar import Alarm, Calendar, Event
from pydantic import BaseModel, Field
from webdav3.client import Client

from webdav3.exceptions import ConnectionException  # isort: skip
from webdav3.exceptions import LocalResourceNotFound  # isort: skip
from webdav3.exceptions import RemoteResourceNotFound  # isort: skip
from webdav3.exceptions import ResourceLocked  # isort: skip
from webdav3.exceptions import WebDavException  # isort: skip

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)


def caldav_safe(func: Callable) -> Callable:
    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> dict:
        op = func.__name__
        try:
            result = func(*args, **kwargs)
            response = {"result": "True"}
            if result is not None:
                response["data"] = result
            return response
        except NotFoundError as e:
            logger.info(f"{op}: resource not found - {e}")
            return {"result": False, "error": "not_found", "details": str(e)}
        except Exception as e:
            logger.error(
                f"{op}: unexpected error - {type(e).__name__}: {e}\
                \nTraceback: {traceback.format_exc()}"
            )
            return {"result": "False", "details": f"{op}: error ({type(e).__name__})"}

    return wrapper


def webdav_safe(func: Callable) -> Callable:
    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> dict:
        op = func.__name__
        try:
            result = func(*args, **kwargs)
            response = {"result": "True"}
            if result is not None:
                response["data"] = result
            return response
        except RemoteResourceNotFound as e:
            logger.error(
                f"{op}: resource not found - {e}\nTraceback: {traceback.format_exc()}"
            )
            return {"result": "False", "details": f"{op}: not found"}
        except LocalResourceNotFound as e:
            logger.error(
                f"{op}: local file not found - {e}\nTraceback: {traceback.format_exc()}"
            )
            return {"result": "False", "details": f"{op}: local file not found"}
        except ResourceLocked as e:
            logger.error(
                f"{op}: resource locked - {e}\nTraceback: {traceback.format_exc()}"
            )
            return {"result": "False", "details": f"{op}: resource locked"}
        except ConnectionException as e:
            logger.error(
                f"{op}: connection failed - {e}\nTraceback: {traceback.format_exc()}"
            )
            return {"result": "False", "details": f"{op}: connection failed"}
        except WebDavException as e:
            logger.error(
                f"{op}: WebDAV error - {e}\nTraceback: {traceback.format_exc()}"
            )
            return {"result": "False", "details": f"{op}: {str(e)}"}
        except Exception as e:
            logger.error(
                f"{op}: unexpected error - {type(e).__name__}: {e}\
                \nTraceback: {traceback.format_exc()}"
            )
            return {"result": "False", "details": f"{op}: error ({type(e).__name__})"}

    return wrapper


class Tools:
    def __init__(self):
        self.valves = self.Valves()
        self.H = self.Helpers(self.valves)

        self._valve_hash = None
        self._webdav_client = None
        self._caldav_client = None

    class Valves(BaseModel):
        NEXTCLOUD_BASE_URL: str = Field("", description="Nextcloud server address")
        WEBDAV_USERNAME: str = Field("")
        NEXTCLOUD_USERNAME: str = Field("")
        NEXTCLOUD_APP_PASSWORD: str = Field("", json_schema_extra={"secret": True})
        SANDBOX_DIR: str = Field(
            default="owuinc",
            description="A relative directory path (or an empty string) \
            that will be prefixed to every file/directory operation performed \
            by the Tools class.",
        )
        DEFAULT_CALENDAR: str = Field(
            default="main",
            description="Default calendar for event operations"
        )
        DEFAULT_TASK_LIST: str = Field(
            default="todo",
            description="Default task list for task operations"
        )
        pass  # required for parsing

    class Helpers:
        def __init__(self, valves):
            self.valves = valves

        def get_valve_hash(self):
            return hash(
                (
                    self.valves.NEXTCLOUD_BASE_URL,
                    self.valves.WEBDAV_USERNAME,
                    self.valves.NEXTCLOUD_USERNAME,
                    self.valves.NEXTCLOUD_APP_PASSWORD,
                )
            )

        @staticmethod
        def validate_path(path, sandbox):
            prefix = sandbox.strip().rstrip("/") + "/"
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
            full_path = prefix + os.path.normpath(path)
            if full_path.startswith(prefix):
                return full_path
            raise Exception("Invalid Path: outside sandbox.")

        @staticmethod
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

        @staticmethod
        def get_cal_type(calendar: Calendar | None = None):
            if not calendar:
                return "none"
            sccs = calendar.get_properties().get(
                "{urn:ietf:params:xml:ns:caldav}supported-calendar-component-set"
            )
            if not sccs:
                return "unknown"
            kinds = [comp.get("name") for comp in sccs if comp.tag.endswith("comp")]
            has_event = "VEVENT" in kinds
            has_todo = "VTODO" in kinds
            if has_event and not has_todo:
                return "event"
            elif has_todo and not has_event:
                return "todo"
            elif has_event and has_todo:
                return "mixed"
            else:
                return "unknown"

    @property
    def webdav_client(self):
        vh = self.H.get_valve_hash()
        if self._webdav_client is None or self._valve_hash != vh:
            self._valve_hash = vh
            base = self.valves.NEXTCLOUD_BASE_URL
            wd_user = self.valves.WEBDAV_USERNAME
            url = f"{base}/remote.php/dav/files/{wd_user}/"
            logger.debug(f"webdav_client url: {url!r}")
            self._webdav_client = Client(
                {
                    "webdav_hostname": url,
                    "webdav_login": self.valves.NEXTCLOUD_USERNAME,
                    "webdav_password": self.valves.NEXTCLOUD_APP_PASSWORD,
                }
            )
        return self._webdav_client

    @property
    def caldav_client(self):
        vh = self.H.get_valve_hash()
        if self._caldav_client is None or self._valve_hash != vh:
            self._valve_hash = vh
            url = f"{self.valves.NEXTCLOUD_BASE_URL}/remote.php/dav/"
            logger.debug(f"caldav_client url: {url!r}")

            self._caldav_client = get_davclient(
                username=self.valves.NEXTCLOUD_USERNAME,
                password=self.valves.NEXTCLOUD_APP_PASSWORD,
                url=url,
                features="nextcloud",
                enable_rfc6764=False,
            )
        return self._caldav_client

    @webdav_safe
    def mkdir(
        self,
        path: str,
    ) -> None:
        """Create new directory"""
        self.webdav_client.mkdir(self.H.validate_path(path, self.valves.SANDBOX_DIR))

    @webdav_safe
    def ls(self, path: str | None = None) -> list[str]:
        """List files and directories"""
        sandbox = self.valves.SANDBOX_DIR
        p = self.H.validate_path(path, sandbox)
        prefix = f"{sandbox.strip().rstrip('/')}/"
        paths = self.webdav_client.list(p)
        parent = p.strip(prefix).strip("/")
        result_list = [p for p in paths if p != prefix and p.strip("/") != parent]
        return result_list

    @webdav_safe
    def write_file(
        self,
        path: str,
        content: Optional[str] = None,
    ) -> None:
        """Write to a file, overwriting existing content"""
        if content is None:
            content = ""
        self.webdav_client.resource(
            self.H.validate_path(path, self.valves.SANDBOX_DIR)
        ).read_from(BytesIO(content.encode("utf-8")))

    @webdav_safe
    def cat(
        self,
        path: str,
    ) -> str:
        """Read a file"""
        buf = BytesIO()
        self.webdav_client.resource(
            self.H.validate_path(path, self.valves.SANDBOX_DIR)
        ).write_to(buf)
        return buf.getvalue().decode("utf-8")

    @webdav_safe
    def append_file(
        self,
        path: str,
        content: Optional[str] = None,
    ) -> None:
        """Append content to file, creating it if it does not exist"""
        if content is None:
            content = ""
        buf = BytesIO()
        res = self.webdav_client.resource(
            self.H.validate_path(path, self.valves.SANDBOX_DIR)
        )
        res.write_to(buf)
        res.read_from(
            BytesIO((buf.getvalue().decode("utf-8") + content).encode("utf-8"))
        )

    @webdav_safe
    def rm(self, paths: list[str]) -> None:
        """Deletes files/directories"""
        C = self.webdav_client
        for p in paths:
            C.clean(self.H.validate_path(p, self.valves.SANDBOX_DIR))

    @webdav_safe
    def mv(
        self,
        src: str,
        dst: str,
    ) -> None:
        """Move/rename a file or directory"""
        self.webdav_client.move(
            remote_path_from=self.H.validate_path(src, self.valves.SANDBOX_DIR),
            remote_path_to=self.H.validate_path(dst, self.valves.SANDBOX_DIR),
        )

    @webdav_safe
    def cp(
        self,
        src: str,
        dst: str,
    ) -> None:
        """Copy a file or directory."""
        self.webdav_client.copy(
            remote_path_from=self.H.validate_path(src, self.valves.SANDBOX_DIR),
            remote_path_to=self.H.validate_path(dst, self.valves.SANDBOX_DIR),
        )

    @caldav_safe
    def get_task_lists(self) -> list[str]:
        """Retrieve available task lists"""
        return [
            c.name
            for c in self.caldav_client.principal().calendars()
            if self.H.get_cal_type(c) == "todo"
        ]

    @caldav_safe
    def get_tasks(self, list_name: str | None = None) -> list[dict]:
        """Retrieve task from specified list"""
        task_map: dict[str, dict] = {}
        list_name = list_name or self.valves.DEFAULT_TASK_LIST
        for todo in (
            self.caldav_client.principal().calendar(name=list_name.strip()).todos()
        ):
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

    @caldav_safe
    def edit_task(
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
    ) -> None:
        """Update task properties by summary or uid"""
        if not (summary or uid):
            raise Exception("must specify summary or uid of task to edit")
        list_name = list_name or self.valves.DEFAULT_TASK_LIST
        cal = self.caldav_client.principal().calendar(name=list_name.strip())
        if uid:
            todo = cal.todo_by_uid(uid)
        elif summary:  # find the uid
            matches = []
            for todo in cal.todos():
                if summary.strip() in todo.component["summary"]:
                    matches.append(todo.component["uid"])
            if len(matches) > 1:
                raise Exception(f"Multiple matches for {summary!r}: {matches}")
            elif len(matches) == 1:
                todo = cal.todo_by_uid(matches[0])
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
        todo.save()

    @caldav_safe
    def delete_task(
        self,
        summary: Optional[str] = None,
        uid: Optional[str] = None,
        list_name: str | None = None,
    ) -> None:
        """Delete task from specified list by summary or uid"""
        if not (summary or uid):
            raise Exception("must specify summary or uid of task to edit")
        list_name = list_name or self.valves.DEFAULT_TASK_LIST
        cal = self.caldav_client.principal().calendar(name=list_name.strip())
        if uid:
            todo = cal.todo_by_uid(uid)
        elif summary:  # find the uid
            matches = []
            for todo in cal.todos():
                if summary.strip() in todo.component["summary"]:
                    matches.append(todo.component["uid"])
            if len(matches) > 1:
                raise Exception(f"Error: Multiple matches for {summary!r}: {matches}")
            elif len(matches) == 1:
                todo = cal.todo_by_uid(matches[0])
        if not todo:
            raise Exception("Error: task not found")
        todo.delete()

    @caldav_safe
    def get_calendars(self) -> list[str]:
        """Retreive all available calendars"""
        return [
            c.name
            for c in self.caldav_client.principal().calendars()
            if self.H.get_cal_type(c) == "event"
        ]

    @caldav_safe
    def create_calendar_event(
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
    ) -> str:
        """Add event to specified calendar."""
        zi = ZoneInfo(__user__["timezone"])
        now = datetime.now(zi).replace(second=0, microsecond=0)
        calendar_name = calendar_name or self.valves.DEFAULT_CALENDAR
        cal = self.caldav_client.principal().calendar(name=calendar_name.strip())

        uid = str(uuid.uuid4())
        e = Event()
        e.add("uid", uid)
        e.add("summary", summary)
        e.add("dtstamp", now)  # required
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
            for r in self.H.parse_reminders(alarms):
                a = Alarm()
                a.add("action", "DISPLAY")
                a.add("trigger", timedelta(minutes=-r.get("minutes")))
                a.add("description", summary)
                e.add_component(a)
        cal.save_event(ical=e)
        return uid

    @caldav_safe
    def add_task(
        self,
        summary: str,
        list_name: str | None = None,
        priority: Optional[int] = 0,
        description: Optional[str] = None,
        categories: Optional[List[str]] = None,
        url: Optional[str] = None,
        location: Optional[str] = None,
    ) -> str:
        """Add task to specified list. Returns uid of created task."""
        list_name = list_name or self.valves.DEFAULT_TASK_LIST
        uid = str(uuid.uuid4())
        p = self.caldav_client.principal()
        valid_lists = [
            c.name for c in p.calendars() if self.H.get_cal_type(c) == "todo"
        ]
        if list_name not in valid_lists:
            logger.error("invalid task list")
            raise Exception("invalid task list")
        p.calendar(name=list_name.strip()).save_todo(
            uid=uid,
            summary=summary,
            priority=priority,
            description=description,
            categories=categories,
            url=url,
            location=location,
        )
        return uid

    @caldav_safe
    def complete_task(
        self,
        summary: str,
        uid: Optional[str] = None,
        list_name: str | None = None,
    ) -> None:
        """Mark a task as completed"""
        list_name = list_name or self.valves.DEFAULT_TASK_LIST
        cal = self.caldav_client.principal().calendar(name=list_name)
        if uid:
            todo = cal.todo_by_uid(uid)
        elif summary:
            matches = []
            for todo in cal.todos():
                if summary.strip() in todo.component["summary"]:
                    matches.append(todo)
            if len(matches) == 0:
                raise Exception(
                    f"Task with summary {summary!r} not found in list {list_name!r}"
                )
            if len(matches) > 1:
                raise Exception(f"multiple matches found for {summary!r}")
            if len(matches) == 1:
                todo = matches[0]
        todo.component["status"] = "COMPLETED"
        todo.save()

    @caldav_safe
    def edit_calendar_event(
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
    ) -> None:
        """Update event properties by summary or uid."""
        if not (summary or uid):
            raise Exception("Error: must provide a summary or uid")
        tz = __user__["timezone"]
        zi = ZoneInfo(tz)
        calendar_name = calendar_name or self.valves.DEFAULT_CALENDAR
        cal = self.caldav_client.principal().calendar(name=calendar_name.strip())
        if uid:
            e = cal.event_by_uid(uid)
        elif summary:
            matches = []
            for e in cal.events():
                if summary.strip() in e.component["summary"]:
                    matches.append(e.component["uid"])
            if len(matches) > 1:
                raise Exception("Error: multiple matches")
            elif len(matches) == 1:
                e = cal.event_by_uid(matches[0])
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
            # remove existing
            valarm_subs = [
                sub for sub in e.component.subcomponents if sub.name == "VALARM"
            ]
            for sub in valarm_subs[:]:  # prevent index shifting
                e.component.subcomponents.remove(sub)
            # add new
            for reminder in self.H.parse_reminders(new_alarms):
                a = Alarm()
                a.add("action", "DISPLAY")
                a.add("trigger", timedelta(minutes=-reminder.get("minutes")))
                a.add("description", e.component["summary"])
                e.component.add_component(a)
        e.save()

    @caldav_safe
    def get_calendar_events(
        self,
        calendar_name: str | None = None,
        __user__: dict = {},
    ) -> list[dict[str, Any]]:
        """Retreive upcoming events on specified calendar"""
        # future events. Prevent RRULE expansion
        event_data = []
        calendar_name = calendar_name or self.valves.DEFAULT_CALENDAR
        for e in (
            self.caldav_client.principal()
            .calendar(name=calendar_name.strip())
            .search(start=datetime.now(ZoneInfo(__user__["timezone"])), expand=False)
        ):
            event_dict = {}
            for field in [
                "uid",
                "summary",
                "description",
                "location",
                "categories",
                "organizer",
                "url",
                "rdate",
                "exdate",
            ]:
                if val := e.component.get(field):
                    event_dict[field] = val
            for field in ("dtstart", "dtend"):
                if val := e.component.get(field):
                    event_dict[field] = val.dt.isoformat()
            if e.component.get("rrule"):
                event_dict["rrule"] = e.component["rrule"].to_ical().decode("utf-8")
            if len(e.component.alarms.times) > 0:
                event_dict["alarms"] = [
                    str(time.trigger) for time in e.component.alarms.times
                ]
            event_data.append(event_dict)
        return event_data

    @caldav_safe
    def delete_calendar_event(
        self,
        uid: Optional[str] = None,
        summary: Optional[str] = None,
        calendar_name: str | None = None,
    ) -> None:
        """Delete event from specified calendar"""
        if not (summary or uid):
            raise Exception("must provide a summary or uid")
        calendar_name = calendar_name or self.valves.DEFAULT_CALENDAR
        cal = self.caldav_client.principal().calendar(name=calendar_name.strip())
        if uid:
            e = cal.event_by_uid(uid)
        elif summary:
            matches = []
            for e in cal.events():
                c = e.component
                if summary.strip() in c["summary"]:
                    matches.append(c["uid"])
            if len(matches) > 1:
                raise Exception(f"multiple matches for summary {summary}")
            elif len(matches) == 1:
                e = cal.event_by_uid(matches[0])
        e.delete()
