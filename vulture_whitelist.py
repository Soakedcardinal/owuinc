"""Vulture whitelist: the OpenWebUI-facing surface of the single-file artifacts.

owuinc.py and startup_context_injector.py are imported by the OpenWebUI
runtime, which calls the Tools methods, Filter.request, and the Valves
classes by reflection. No code in this repo calls them, so without this
whitelist vulture reports the whole public API as unused. A name referenced
here counts as used; the `X.y` lines reference methods and attributes.

After adding or renaming a public tool method or valve, re-check with:
    uv run vulture owuinc/owuinc.py startup_context_injector.py vulture_whitelist.py
"""

Tools
Tools.Valves
Tools.calendars
Tools.task_lists
Tools.mkdir
Tools.ls
Tools.find
Tools.grep
Tools.write
Tools.cat
Tools.append
Tools.edit
Tools.rm
Tools.mv
Tools.cp
Tools.stat
Tools.tasks
Tools.add_task
Tools.edit_task
Tools.complete_task
Tools.delete_task
Tools.create_calendar_event
Tools.edit_calendar_event
Tools.calendar_events
Tools.delete_calendar_event

Filter
Filter.Valves
Filter.request
