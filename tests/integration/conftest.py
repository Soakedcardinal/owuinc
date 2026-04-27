"""Integration test fixtures for CalDAV (Radicale) and WebDAV (WsgiDAV) testing.

All fixtures and tests are async to match the async tool methods in owuinc.py.
"""

import logging
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
import pytest_asyncio

logger = logging.getLogger(__name__)

# ============================================================================
# RADICALE SERVER FIXTURE (CalDAV Integration Tests)
# ============================================================================

RADICALE_PORT = 5232


@pytest.fixture(scope="session")
def radicale_storage_dir():
    """Create temporary storage directory for Radicale.

    Yields:
        Path: Temp directory where Radicale stores collections

    Side effect: Directory is cleaned up after session ends.
    """
    storage_dir = Path(tempfile.mkdtemp(prefix="radicale_test_"))
    logger.info(f">>> Created Radicale storage dir: {storage_dir}")
    try:
        yield storage_dir
    finally:
        logger.info(f">>> Cleaning up Radicale storage dir: {storage_dir}")
        shutil.rmtree(storage_dir, ignore_errors=True)


def _wait_for_server(url: str, timeout: float = 10) -> None:
    """Poll a server until ready or timeout.

    Accepts 200/207 (Radicle anonymous) or 401 (WsgiDAV with auth) as
    indicators that the server process is up and responding.
    """
    import urllib.error
    import urllib.request

    start = time.time()
    attempt = 0
    while time.time() - start < timeout:
        attempt += 1
        try:
            req = urllib.request.Request(url, method="OPTIONS")
            try:
                with urllib.request.urlopen(req, timeout=1) as resp:
                    status = resp.status
                    if status in (200, 207):
                        elapsed = time.time() - start
                        logger.info(
                            f">>> Server ready at {url} "
                            f"(HTTP {status}, attempt {attempt}, {elapsed:.1f}s)"
                        )
                        return
                    logger.debug(
                        f">>> Poll attempt {attempt}: HTTP {status} "
                        f"(waiting for 200/207/401)"
                    )
            except urllib.error.HTTPError as http_err:
                if http_err.code == 401:
                    elapsed = time.time() - start
                    logger.info(
                        f">>> Server ready at {url} "
                        f"(HTTP 401, attempt {attempt}, {elapsed:.1f}s)"
                    )
                    return
                logger.debug(
                    f">>> Poll attempt {attempt}: HTTP {http_err.code} "
                    f"(waiting for 200/207/401)"
                )
        except Exception as exc:
            logger.debug(f">>> Poll attempt {attempt}: not ready yet ({exc})")
        time.sleep(0.1)

    elapsed = time.time() - start
    raise TimeoutError(f"Server not ready after {elapsed:.1f}s ({attempt} attempts)")


@pytest.fixture(scope="session")
def radicale_server(radicale_storage_dir):
    """Start Radicale CalDAV server once per session with basic auth.

    Yields:
        dict: Server configuration with url, username, password

    Note: Uses htpasswd with MD5 crypt encryption, fully supported by both
          Radicale and the caldav library.
    """
    fixtures_dir = Path(__file__).parent / "fixtures"
    htpasswd_file = fixtures_dir / ".htpasswd"

    logger.info(f">>> htpasswd file: {htpasswd_file}")
    logger.info(f">>> htpasswd exists: {htpasswd_file.exists()}")
    if htpasswd_file.exists():
        logger.info(f">>> htpasswd contents: {htpasswd_file.read_text().strip()!r}")

    # Create config file in storage directory
    config_file = radicale_storage_dir / "config.ini"
    config_content = f"""[server]
hosts = 127.0.0.1:{RADICALE_PORT}
timeout = 30

[auth]
type = htpasswd
htpasswd_filename = {htpasswd_file}
htpasswd_encryption = md5

[storage]
filesystem_folder = {radicale_storage_dir}/collection-root

[rights]
type = owner_only

[logging]
level = warning
"""
    config_file.write_text(config_content)
    logger.info(f">>> Wrote Radicale config: {config_file}")

    import shutil
    import sys

    radicale_exe = shutil.which("radicale")
    if radicale_exe is None:
        # Try relative to sys.executable (e.g., .venv/bin/radicale)
        venv_bin = Path(sys.executable).parent
        radicale_exe = venv_bin / "radicale"
        if not radicale_exe.exists():
            raise RuntimeError(
                "Radicale not found on PATH or in .venv/bin — "
                "install radicale or ensure it's on PATH"
            )
        radicale_exe = str(radicale_exe)

    logger.info(f">>> Radicale executable: {radicale_exe}")

    # Start Radicale in its own process group so we can kill all children
    logger.info(
        f">>> Starting Radicale on port {RADICALE_PORT} "
        f"(cmd: {radicale_exe} --config {config_file})"
    )
    proc = subprocess.Popen(
        [str(radicale_exe), "--config", str(config_file)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    logger.info(f">>> Radicale PID: {proc.pid}, process group: {os.getpgid(proc.pid)}")

    try:
        _wait_for_server(f"http://127.0.0.1:{RADICALE_PORT}")

        server_info = {
            "url": f"http://127.0.0.1:{RADICALE_PORT}",
            "username": "testuser",
            "password": "testpass123",
        }
        logger.info(f">>> Yielding Radicale server config: {server_info['url']}")
        yield server_info
    finally:
        logger.info(f">>> Tearing down Radicale (PID {proc.pid})")
        # Kill entire process group to catch orphaned children
        try:
            pgid = os.getpgid(proc.pid)
            logger.info(f">>> Sending SIGTERM to process group {pgid}")
            os.killpg(pgid, signal.SIGTERM)
        except (OSError, ProcessLookupError) as exc:
            logger.info(f">>> SIGTERM failed (process may already be dead): {exc}")
        try:
            proc.wait(timeout=3)
            logger.info(f">>> Radicale exited cleanly (returncode={proc.returncode})")
        except subprocess.TimeoutExpired:
            logger.info(">>> Radicale did not exit after SIGTERM, sending SIGKILL")
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            proc.wait()
            logger.info(f">>> Radicale killed (returncode={proc.returncode})")

        # Verify port is free
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        port_open = sock.connect_ex(("127.0.0.1", RADICALE_PORT)) == 0
        sock.close()
        if port_open:
            logger.warning(
                f">>> WARNING: Port {RADICALE_PORT} still bound after teardown!"
            )
        else:
            logger.info(f">>> Port {RADICALE_PORT} confirmed free after teardown")


@pytest_asyncio.fixture(scope="function")
async def caldav_tools(radicale_server):
    """Create Tools instance configured for Radicale CalDAV server.

    Args:
        radicale_server: fixture providing server configuration

    Yields:
        Tools: Configured owuinc.Tools instance

    Note: Overrides caldav_client to use async Radicale client with
          basic auth instead of Nextcloud's /remote.php/dav prefix.
    """
    from caldav.aio import get_async_davclient

    from owuinc.owuinc import Tools

    logger.info(">>> Creating caldav_tools fixture")
    t = Tools()

    t.valves.NEXTCLOUD_BASE_URL = radicale_server["url"].rstrip("/")
    t.valves.WEBDAV_USERNAME = radicale_server["username"]
    t.valves.NEXTCLOUD_USERNAME = radicale_server["username"]
    t.valves.NEXTCLOUD_APP_PASSWORD = radicale_server["password"]
    t.valves.SANDBOX_DIR = "owuinc"
    t.valves.CALENDAR_WHITELIST = "Personal"
    t.valves.TASK_LIST_WHITELIST = "Tasks"

    logger.info(
        f">>> Tools valves configured: "
        f"base_url={t.valves.NEXTCLOUD_BASE_URL}, "
        f"username={t.valves.NEXTCLOUD_USERNAME}, "
        f"sandbox={t.valves.SANDBOX_DIR}"
    )

    original_caldav_client = Tools.caldav_client

    @property
    def rad_caldav_client(self):
        return get_async_davclient(
            url=self.valves.NEXTCLOUD_BASE_URL,
            username=radicale_server["username"],
            password=radicale_server["password"],
            features="radicale",
            enable_rfc6764=False,
        )

    Tools.caldav_client = rad_caldav_client

    try:
        logger.info(">>> Yielding caldav_tools instance")
        yield t
    finally:
        Tools.caldav_client = original_caldav_client
        logger.info(">>> Restored original Tools.caldav_client property")


# ============================================================================
# WSGIDAV SERVER FIXTURE (WebDAV Integration Tests)
# ============================================================================

WSGIDAV_PORT = 5233


@pytest.fixture(scope="session")
def wsgidav_storage_dir():
    """Create temporary storage directory for WsgiDAV with Nextcloud URL structure.

    Creates: <storage>/remote.php/dav/files/testuser/

    Yields:
        Path: Temp directory where WsgiDAV serves files

    Side effect: Directory is cleaned up after session ends.
    """
    storage_dir = Path(tempfile.mkdtemp(prefix="wsgidav_test_"))
    # Mirror Nextcloud's URL path structure
    files_dir = storage_dir / "remote.php" / "dav" / "files" / "testuser"
    files_dir.mkdir(parents=True)
    logger.info(f">>> Created WsgiDAV storage dir: {storage_dir}")
    logger.info(f">>> Files root: {files_dir}")
    try:
        yield storage_dir
    finally:
        logger.info(f">>> Cleaning up WsgiDAV storage dir: {storage_dir}")
        shutil.rmtree(storage_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def wsgidav_server(wsgidav_storage_dir):
    """Start WsgiDAV server with basic auth and Nextcloud URL structure.

    URL mirrors Nextcloud:
        http://127.0.0.1:5233/remote.php/dav/files/testuser/

    Uses a separate script directory so the server script doesn't appear
    in WsgiDAV's file listings.

    Yields:
        dict: Server configuration with url, username, password
    """
    script_dir = Path(tempfile.mkdtemp(prefix="wsgidav_script_"))
    script_path = script_dir / "server.py"

    script_path.write_text(
        f"""
from wsgidav.fs_dav_provider import FilesystemProvider
from wsgidav.wsgidav_app import WsgiDAVApp
from wsgidav.dc.simple_dc import SimpleDomainController
from cheroot import wsgi

provider = FilesystemProvider(
    {str(wsgidav_storage_dir)!r}, readonly=False, fs_opts={{}}
)
config = {{
    "host": "127.0.0.1",
    "port": {WSGIDAV_PORT},
    "provider_mapping": {{"/": provider}},
    "http_authenticator": {{
        "domain_controller": None,
        "accept_basic": True,
        "accept_digest": False,
        "default_to_digest": False,
    }},
    "simple_dc": {{
        "user_mapping": {{
            "*": {{
                "testuser": {{
                    "password": "testpass123"
                }}
            }}
        }}
    }},
    "verbose": 0,
    "lock_storage": True,
    "property_manager": True,
}}
app = WsgiDAVApp(config)
server = wsgi.Server(
    bind_addr=("127.0.0.1", {WSGIDAV_PORT}),
    wsgi_app=app,
    server_name="wsgidav-test",
)
server.start()
"""
    )

    logger.info(f">>> WsgiDAV script: {script_path}")

    import sys

    python_exe = Path(sys.executable)
    if not python_exe.exists():
        raise RuntimeError(f"Python executable not found: {python_exe}")

    logger.info(
        f">>> Starting WsgiDAV on port {WSGIDAV_PORT} "
        f"(cmd: {python_exe} {script_path})"
    )
    proc = subprocess.Popen(
        [str(python_exe), str(script_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    logger.info(f">>> WsgiDAV PID: {proc.pid}")

    try:
        _wait_for_server(f"http://127.0.0.1:{WSGIDAV_PORT}")

        server_info = {
            "url": f"http://127.0.0.1:{WSGIDAV_PORT}",
            "username": "testuser",
            "password": "testpass123",
        }
        base = server_info["url"]
        logger.info(f">>> Yielding WsgiDAV server config: {base}/remote.php/dav/")
        yield server_info
    finally:
        logger.info(f">>> Tearing down WsgiDAV (PID {proc.pid})")
        try:
            pgid = os.getpgid(proc.pid)
            logger.info(f">>> Sending SIGTERM to process group {pgid}")
            os.killpg(pgid, signal.SIGTERM)
        except (OSError, ProcessLookupError) as exc:
            logger.info(f">>> SIGTERM failed (process may already be dead): {exc}")
        try:
            proc.wait(timeout=3)
            logger.info(f">>> WsgiDAV exited cleanly (returncode={proc.returncode})")
        except subprocess.TimeoutExpired:
            logger.info(">>> WsgiDAV did not exit after SIGTERM, sending SIGKILL")
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            proc.wait()
            logger.info(f">>> WsgiDAV killed (returncode={proc.returncode})")

        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        port_open = sock.connect_ex(("127.0.0.1", WSGIDAV_PORT)) == 0
        sock.close()
        if port_open:
            logger.warning(
                f">>> WARNING: Port {WSGIDAV_PORT} still bound after teardown!"
            )
        else:
            logger.info(f">>> Port {WSGIDAV_PORT} confirmed free after teardown")

        shutil.rmtree(script_dir, ignore_errors=True)


@pytest_asyncio.fixture(scope="function")
async def webdav_tools(wsgidav_server):
    """Create Tools instance configured for WsgiDAV with Nextcloud URL structure.

    URL: http://127.0.0.1:5233/remote.php/dav/files/testuser/

    Args:
        wsgidav_server: fixture providing server configuration

    Yields:
        Tools: Configured owuinc.Tools instance
    """
    from aiowebdav2 import Client as WebDAVClient

    from owuinc.owuinc import Tools

    logger.info(">>> Creating webdav_tools fixture")
    t = Tools()

    t.valves.NEXTCLOUD_BASE_URL = wsgidav_server["url"].rstrip("/")
    t.valves.WEBDAV_USERNAME = wsgidav_server["username"]
    t.valves.NEXTCLOUD_USERNAME = wsgidav_server["username"]
    t.valves.NEXTCLOUD_APP_PASSWORD = wsgidav_server["password"]
    t.valves.SANDBOX_DIR = "owuinc"

    logger.info(
        f">>> Tools valves configured: "
        f"base_url={t.valves.NEXTCLOUD_BASE_URL}, "
        f"webdav_user={t.valves.WEBDAV_USERNAME}, "
        f"sandbox={t.valves.SANDBOX_DIR}"
    )

    original_webdav_client = Tools._webdav_client

    def wsgi_webdav_client(self):
        return WebDAVClient(
            f"{self.valves.NEXTCLOUD_BASE_URL}/remote.php/dav/files/"
            f"{self.valves.WEBDAV_USERNAME}/",
            self.valves.NEXTCLOUD_USERNAME,
            self.valves.NEXTCLOUD_APP_PASSWORD,
        )

    Tools._webdav_client = wsgi_webdav_client

    try:
        logger.info(">>> Yielding webdav_tools instance")
        yield t
    finally:
        Tools._webdav_client = original_webdav_client
        logger.info(">>> Restored original Tools._webdav_client method")
