# 1. Add to OpenWebUI
* Navigate to Tools > + New Tool
* Enter Tool Name and description e.g. `owuinc`
* Paste the contents of `owuinc.py`
* Click Save > Confirm
* Click the gear icon next to owuinc to open Valves configuration

# 2. Configure Valves
* In NextCloud Files settings, identify your WebDAV URL, e.g`https://your-nextcloud-domain.com/remote.php/dav/files/<WEBDAV_USERNAME>`
* Copy the `<WEBDAV_USERNAME>` from the URL
* In NextCloud > Personal Settings > Security, create an app password named e.g. `owuinc`
* In OpenWebUI Valves, enter:
    * `BASE_URL`: your nextcloud server (for docker compose setups, this can be as simple as `http://<nextcloud-container-name>`)
    * `NEXTCLOUD_USERNAME`: your webdav username
    * `NEXTCLOUD_PASSWORD`: your app password

# 3. Configure Sandbox
* Set `SANDBOX_DIRECTORY` to e.g. `/some/dir` limit unwanted file access

# 4. Configure Model
* Select the checkbox to enable the tool
* Add this to the system prompt:

```text
Task Priorities: 1 = high, 9 = low, 0 = none
Calendar Functions: Provide `start` and `end` arguments as an ISO 8601-style string without a timezone offset, e.g. `2026-02-01T15:30`.
```