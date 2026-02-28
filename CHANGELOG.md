# Changelog

## [2.0.0] - 2026-02-28

### ⚠️ Breaking Changes

**Calendar and task list whitelisting now required for all operations.**

The security model has been updated to enforce explicit whitelisting of calendars and task lists. This is a **breaking change** for existing users.

The defaults are set to `owuinc` to demonstrate per-model sandboxing:

```
CALENDAR_WHITELIST: owuinc
TASK_LIST_WHITELIST: owuinc

DEFAULT_CALENDAR: owuinc
DEFAULT_TASK_LIST: owuinc
```

**Simple setup:** Create e.g `owuinc` calendar and `owuinc` task list in Nextcloud, then add them to the whitelists.

**Advanced setup:** Create separate calendars/task lists per model (e.g., `agent1-calendar`, `agent2-calendar`) for complete isolation.

(ONLY whitelisted calendars/task lists are accessible)

---

### ⚠️ Breaking Changes

- Calendar and task list whitelisting is now **required** for all operations
- Empty whitelist = no access to any calendars/task lists  
- Default calendar/task list must also be whitelisted
- All 9 calendar/task operations validate against whitelist

#### What Changed

1. **New required configuration fields:**
   - `CALENDAR_WHITELIST`: Comma-separated list of allowed calendars
   - `TASK_LIST_WHITELIST`: Comma-separated list of allowed task lists

2. **Default values updated:**
   - `DEFAULT_CALENDAR`: Set to `"owuinc"` (demonstrates per-model sandboxing)
   - `DEFAULT_TASK_LIST`: Set to `"owuinc"` (demonstrates per-model sandboxing)
   
   These defaults are suggestions - change them to match your setup.

3. **New validation:**
   - All calendar/task operations now validate against whitelist
   - Empty whitelist = no access (strict security)
   - Whitelist must be explicitly configured for any operation to work
   - Default calendar/task list must also be whitelisted

4. **`get_calendars()` and `get_task_lists()`:**
   - Now return only whitelisted calendars/task lists
   - Returns empty list if whitelist not configured

### Why This Change?

This change enforces a secure sandboxing model where only explicitly configured calendars and task lists are accessible. This prevents accidental access to sensitive calendars or task lists and gives users full control over their data exposure.

## [1.0.0]
- Initial public release