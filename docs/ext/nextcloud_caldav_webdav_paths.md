# Nextcloud WebDAV/CalDAV Path Structure

## Key Differences from Radicale (Critical for Testing)

### 1. WebDAV File Operations Path

**Nextcloud:**
```
https://cloud.example.com/remote.php/dav/files/USERNAME/
```

**Example paths:**
- User files: `/remote.php/dav/files/john/`
- Subdirectory: `/remote.php/dav/files/john/Documents/`
- File: `/remote.php/dav/files/john/Documents/file.txt`

**Radicale (default):**
```
http://localhost:5232/
```
- No `/remote.php/dav/files/USERNAME/` prefix
- Root path is the user's collection

### 2. CalDAV Calendar Operations Path

**Nextcloud:**
```
https://cloud.example.com/remote.php/dav
```

Used for:
- Principal discovery: `PROPFIND /remote.php/dav`
- Calendar home set: Returns `/remote.php/dav/calendars/USERNAME/`
- Calendar access: `/remote.php/dav/calendars/USERNAME/calendar-name/`

**Radicale (default):**
```
http://localhost:5232/
```
- Principal discovery: `PROPFIND /`
- No Nextcloud-specific path structure
- Calendars at root level

### 3. URL Construction in Code

From `owuinc/owuinc.py`:

```python
# WebDAV client (line 308-312)
base = self.valves.NEXTCLOUD_BASE_URL
wd_user = self.valves.WEBDAV_USERNAME
url = f"{base}/remote.php/dav/files/{wd_user}/"

# CalDAV client (line 325-327)
base = self.valves.NEXTCLOUD_BASE_URL
url = f"{base}/remote.php/dav"
```

**This means:**
- All production code assumes Nextcloud path structure
- Radicale tests fail because it doesn't use `/remote.php/dav` prefix

## Solution for Testing

### Fix: Override caldav_client property in test fixture

In `tests/integration/conftest.py`, override the caldav_client to use Radicale's root URL:

```python
@property
def rad_caldav_client(self):
    return get_davclient(
        url=self.valves.NEXTCLOUD_BASE_URL,  # No /remote.php/dav for Radicale
        username="testuser",
        password="testpass123",
        features="radicale",
        enable_rfc6764=False,
    )

Tools.caldav_client = rad_caldav_client
```

### Authentication: Use htpasswd basic auth

Radicale's `type = none` authentication causes issues with the caldav library because it still sends WWW-Authenticate headers that confuse the client. 

**Solution:** Use htpasswd basic auth which is fully supported by both Radicale and the caldav library.

```ini
[auth]
type = htpasswd
htpasswd_filename = /path/to/.htpasswd
htpasswd_encryption = md5
```

Generate password hash: `openssl passwd -apr1`

## Testing Strategy (Current Implementation)

### get_calendars() Test (Working with Real Radicale Server)

**Status:** ✅ PASSING - Uses real Radicale server with proper auth

Implementation:
1. Start Radicale server with htpasswd basic auth (md5 encryption)
2. Override `caldav_client` property to use Radicale path structure
3. Call `get_calendars()` against the real server
4. Verify result is correct

This tests the full code path without mocking - using a real CalDAV server running on localhost.

## Calendar Naming

**Nextcloud:**
- User has calendar named "Personal" by default
- Also "Birthday" calendar if enabled
- "Tasks" calendar for todo lists

**Radicale:**
- Starts with no calendars by default
- Calendars created via MKCOL or through API
- Test fixtures create necessary test data programmatically

## Key Insights

**Path Structure:**
Nextcloud uses custom URL structures (`/remote.php/dav`) that differ from standard Radicale. Fix by overriding the client in test fixtures to use appropriate paths for each server.

**Authentication:**
Radicale's `type = none` still sends WWW-Authenticate headers which cause the caldav library to fail. Use htpasswd with md5 encryption instead - fully compatible with both servers and clients.
