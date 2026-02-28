# Changelog


## [2.0.0] - 2026-02-28

### ⚠️ Breaking Changes

**Calendar and task list whitelisting now required for all operations.**

The security model has been updated to enforce explicit whitelisting of calendars and task lists. This is a **breaking change** for existing users.

#### Migration Required

If you were using version 1.x, you must update your Valves configuration:

**Before (v1.x):**
```
DEFAULT_CALENDAR: main
DEFAULT_TASK_LIST: todo
```
(Calendars and task lists were accessible by default)

**After (v2.0.0):**
```
CALENDAR_WHITELIST: work, personal
DEFAULT_CALENDAR: work

TASK_LIST_WHITELIST: todo, shopping
DEFAULT_TASK_LIST: todo
```
(ONLY whitelisted calendars/task lists are accessible)

#### What Changed

1. **New required configuration fields:**
   - `CALENDAR_WHITELIST`: Comma-separated list of allowed calendars
   - `TASK_LIST_WHITELIST`: Comma-separated list of allowed task lists

2. **Default values changed:**
   - `DEFAULT_CALENDAR`: Changed from `"main"` to `""`
   - `DEFAULT_TASK_LIST`: Changed from `"todo"` to `""`

3. **New validation:**
   - All calendar/task operations now validate against whitelist
   - Empty whitelist = no access (strict security)
   - Whitelist must be explicitly configured for any operation to work

4. **`get_calendars()` and `get_task_lists()`:**
   - Now return only whitelisted calendars/task lists
   - Returns empty list if whitelist not configured

### Why This Change?

This change enforces a secure sandboxing model where only explicitly configured calendars and task lists are accessible. This prevents accidental access to sensitive calendars or task lists and gives users full control over their data exposure.

## [1.0.0]
- Initial public release