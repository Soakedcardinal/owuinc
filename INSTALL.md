# 1. Add to OpenWebUI
* Navigate to Tools > + New Tool
* Enter Name and description e.g. `owuinc`
* Paste the contents of [`owuinc.py`](./owuinc/owuinc.py)
* Click Save > Confirm
* Click the gear icon next to the new `owuinc` tool to open Valves

# 2. Configure Valves
* NextCloud > 

* Go to NextCloud 
* In the Files app > Files settings, locate your WebDAV username from the WebDAV URL `https://your-nextcloud-domain.com/remote.php/dav/files/<WEBDAV_USERNAME>`

* Under Profile Icon > Personal Settings > Security, create an app password e.g. `owuinc`

* Go back to OpenWebUI and fill in the Valves
    * `Webdav Username` = <WEBDAV_USERNAME> from NextCloud files settings WebDAV URL
    * Nextcloud Base URL
    * Nextcloud Username
    * Nextcloud App Password

* Set the `Sandbox Dir` valve to somewhere under your nextcloud root to prevent unwanted file access. Must be a relative folder name (e.g. `sandbox`), or empty to use the root. 
* No leading `/` – the code adds it.

* Fill in `Default Calendar` and `Default Task List` to your match your desired calendar/task list names. If you don't specify, the code will try to use `main` calendar and `todo` task lists - if these don't exist it will cause an error.

# 3. Configure Model
* Pick or create a model to use e.g. `owuinc`.
* Open the model page in OpenWebUI > Workspace > Models > `owuinc`

* Add to the system prompt (update the defaults to match your nextcloud setup):
```text
You are a digital assistant.

Task Priorities: 1 = high, 9 = low, 0 = none
Calendar Functions: Provide `start` and `end` arguments as an ISO 8601-style string without a timezone offset, e.g. `2026-02-01T15:30`. 
Default `calendar_name`: `main`
Default `list_name`: `todo`
```

* Set Advanced Params > Show > Function Calling to `Native`
* Under Tools, tick the checkbox to enable the `owuinc` tool
* Press Save & Update

