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

Pick an existing model, or create one to use. For this example, we will set up an agent named `bot`.

### 1. Add to OpenWebUI
* Navigate to Tools > + New Tool
* Enter Name and description e.g. `owuinc_bot`
* Paste the contents of [`owuinc.py`](./owuinc/owuinc.py)
* Click Save > Confirm

### 2. Create Calendar/Task Lists
* NextCloud Calendar app > `+` > New calendar with task list > call it `bot`

### 3. Configure Valves
* In NextCloud Files app > Files settings
* Identify your `<WEBDAV_USERNAME>` from the WebDAV URL `https://your-nextcloud-domain.com/remote.php/dav/files/<WEBDAV_USERNAME>`

* Under Profile Icon > Personal Settings > Security, create an app password e.g. `owuinc_bot`
* In OpenWebUI > gear icon next to `owuinc_bot` tool > fill in the Valves
    * `Webdav Username` ( from WebDAV URL)
    * `Nextcloud Base URL` (nextcloud server address)
    * `Nextcloud Username` (shown next to app password)
    * `Nextcloud App Password`
    * Set `Sandbox Dir` to `owuinc` 
     * Set `Default Calendar` to `Personal`
     * Set `Default Task List` to `Tasks`
     * Set `Calendar Whitelist` to `Personal`
     * Set `Task List Whitelist` to `Tasks`
* Press save

IMPORTANT: Calendars and task lists are ONLY accessible if explicitly listed in the respective whitelist. Both the whitelist AND default values must be configured for operations to work.

### 4. Configure Model
* OpenWebUI > Workspace > Models > `bot`
* Add to the system prompt

```text
Task Priorities: 1 = high, 9 = low, 0 = none
Task/Event operations: Only use Uids internally; never include them in responses.
Calendar Functions: Provide `start` and `end` arguments as an ISO 8601-style string without a timezone offset, e.g. `2026-02-01T15:30`. 
Default `calendar_name`: `Personal`
Default `list_name`: `Tasks`
```

IMPORTANT: update the defaults to match your chosen task list / calendar.

* Ensure Advanced Params > Show > Function Calling is set to `Native`
* Under Tools, tick the checkbox to enable the `owuinc_bot` tool
* Press Save & Update
