# owuinc

Connect OpenWebUI Models to Nextcloud.

## Features

### File Operations
*   `mkdir`, `ls`, `mv`, `cp`, `rm`
*   `write_file`, `cat`, `append_file`

### Task Management
*   Create, read, edit, & delete tasks
*   Support sub-tasks

### Calendar Events
*   Create, read, edit, & delete events
*   Support for recurring events
*   Support Alarms

## Security
*   **Configurable Sandbox**: Prevent the model from accessing unauthorized directories.

## Setup

### 1. Add to OpenWebUI
* Navigate to Tools > + New Tool
* Enter Name and description e.g. `owuinc`
* Paste the contents of [`owuinc.py`](./owuinc/owuinc.py)
* Click Save > Confirm

### 2. Configure Valves
* In NextCloud Files app > Files settings
* locate your WebDAV username in the WebDAV URL `https://your-nextcloud-domain.com/remote.php/dav/files/<WEBDAV_USERNAME>`
* Under Profile Icon > Personal Settings > Security, create an app password e.g. `owuinc`
* In OpenWebUI, click the gear icon next to the `owuinc` tool and fill in the Valves
    * `Webdav Username` = <WEBDAV_USERNAME> (from WebDAV URL)
    * `Nextcloud Base URL` (nextcloud server address)
    * `Nextcloud Username` (shown next to app password)
    * `Nextcloud App Password`
    * Set `Sandbox Dir` valve to somewhere under your nextcloud root. Must be a relative folder name (e.g. `sandbox`). No leading `/`. Leave empty to use the root (NOT RECOMMENDED).
* Configure calendars and task lists:
    * Set `Default Calendar` to a calendar name in your whitelist
    * Set `Default Task List` to a task list name in your whitelist
    * Set `Calendar Whitelist` to comma-separated list of allowed calendars (e.g., `work, personal`)
    * Set `Task List Whitelist` to comma-separated list of allowed task lists (e.g., `main, shopping`)
* IMPORTANT: Calendars and task lists are ONLY accessible if explicitly listed in the respective whitelist. Both the whitelist AND default values must be configured for operations to work.

### 3. Configure Model
* Pick or create a model to use e.g. `owuinc`.
* Open the model page in OpenWebUI > Workspace > Models > `owuinc`
* Add to the system prompt (update the defaults to match your valves):
```text

Task Priorities: 1 = high, 9 = low, 0 = none
Task/Event operations: Only use Uids internally; never include them in responses.
Calendar Functions: Provide `start` and `end` arguments as an ISO 8601-style string without a timezone offset, e.g. `2026-02-01T15:30`. 
Default `calendar_name`: `owuinc`
Default `list_name`: `owuinc-tasks`
```
* Set Advanced Params > Show > Function Calling to `Native`
* Under Tools, tick the checkbox to enable the `owuinc` tool
* Press Save & Update
