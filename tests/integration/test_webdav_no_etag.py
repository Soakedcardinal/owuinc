"""Real-server regression for resources that expose no getetag.

WsgiDAV's stock FilesystemProvider always returns a getetag, so a resource that
omits it cannot be reproduced with the standard fixture. This module stands up a
WsgiDAV instance whose provider forces ``get_etag()`` to ``None``, making the
real aiowebdav2 client observe the same state as an ETag-less server (Nextcloud
shares, some reverse-proxied WebDAV, providers that disable ETags).

The append/edit fail-closed policy and the create path are exercised against a
real HTTP client, not a fake, so a regression in how the tool distinguishes
"exists without ETag" from "missing" or "exists with ETag" is caught here too.
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

NO_ETAG_PORT = 5234


def _wait_for_server(url: str, timeout: float = 10) -> None:
    import urllib.error
    import urllib.request

    start = time.time()
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(url, method="OPTIONS")
            try:
                with urllib.request.urlopen(req, timeout=1) as resp:
                    if resp.status in (200, 207):
                        return
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    return
        except Exception:
            pass
        time.sleep(0.1)
    raise TimeoutError(f"Server not ready after {timeout}s at {url}")


@pytest.fixture(scope="session")
def no_etag_server():
    """WsgiDAV whose provider strips getetag from every resource."""
    storage_dir = Path(tempfile.mkdtemp(prefix="wsgidav_noetag_storage_"))
    files_dir = storage_dir / "remote.php" / "dav" / "files" / "testuser"
    files_dir.mkdir(parents=True)
    script_dir = Path(tempfile.mkdtemp(prefix="wsgidav_noetag_script_"))
    script_path = script_dir / "server.py"
    script_path.write_text(f"""
from wsgidav.fs_dav_provider import FilesystemProvider
from wsgidav.wsgidav_app import WsgiDAVApp
from cheroot import wsgi


def _no_etag(res):
    if res is not None:
        res.get_etag = lambda: None
        res.support_etag = lambda: False
    return res


class NoEtagProvider(FilesystemProvider):
    def get_resource_inst(self, path, environ):
        return _no_etag(super().get_resource_inst(path, environ))


provider = NoEtagProvider({str(storage_dir)!r}, readonly=False, fs_opts={{}})
config = {{
    "host": "127.0.0.1",
    "port": {NO_ETAG_PORT},
    "provider_mapping": {{"/": provider}},
    "http_authenticator": {{
        "domain_controller": None,
        "accept_basic": True,
        "accept_digest": False,
        "default_to_digest": False,
    }},
    "simple_dc": {{
        "user_mapping": {{
            "*": {{"testuser": {{"password": "testpass123"}}}}
        }}
    }},
    "verbose": 0,
    "lock_storage": True,
    "property_manager": True,
}}
app = WsgiDAVApp(config)
server = wsgi.Server(
    bind_addr=("127.0.0.1", {NO_ETAG_PORT}),
    wsgi_app=app,
    server_name="wsgidav-noetag",
)
server.start()
""")

    import sys

    python_exe = Path(sys.executable)
    proc = subprocess.Popen(
        [str(python_exe), str(script_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        _wait_for_server(f"http://127.0.0.1:{NO_ETAG_PORT}")
        yield {
            "url": f"http://127.0.0.1:{NO_ETAG_PORT}",
            "username": "testuser",
            "password": "testpass123",
        }
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            proc.wait()
        shutil.rmtree(storage_dir, ignore_errors=True)
        shutil.rmtree(script_dir, ignore_errors=True)


@pytest_asyncio.fixture(scope="function")
async def no_etag_tools(no_etag_server):
    from aiowebdav2 import Client as WebDAVClient

    from owuinc.owuinc import Tools

    t = Tools()
    t.valves.NEXTCLOUD_BASE_URL = no_etag_server["url"].rstrip("/")
    t.valves.WEBDAV_USERNAME = no_etag_server["username"]
    t.valves.NEXTCLOUD_USERNAME = no_etag_server["username"]
    t.valves.NEXTCLOUD_APP_PASSWORD = no_etag_server["password"]
    t.valves.SANDBOX_DIR = "owuinc"

    original = Tools._webdav_client

    def patched(self):
        return WebDAVClient(
            f"{self.valves.NEXTCLOUD_BASE_URL}/remote.php/dav/files/{self.valves.WEBDAV_USERNAME}/",
            self.valves.NEXTCLOUD_USERNAME,
            self.valves.NEXTCLOUD_APP_PASSWORD,
        )

    Tools._webdav_client = patched
    try:
        yield t
    finally:
        Tools._webdav_client = original


@pytest.mark.asyncio
async def test_server_genuinely_omits_getetag(no_etag_tools):
    from owuinc.owuinc import _webdav_path, validate_path

    client = no_etag_tools._webdav_client()
    try:
        await no_etag_tools.mkdir("noetag")
        res_path = _webdav_path(validate_path("noetag/probe.txt", no_etag_tools.valves))
        await client.execute_request("upload", res_path, data=b"seed")
        exists, etag = await no_etag_tools._get_etag_state(client, res_path)
        assert exists is True, "check() must still detect the resource"
        assert etag is None, "server must not expose a getetag"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_append_no_etag_fails_closed_preserves_content(no_etag_tools):
    from owuinc.owuinc import _webdav_path, validate_path

    client = no_etag_tools._webdav_client()
    try:
        await no_etag_tools.mkdir("noetag")
        res_path = _webdav_path(
            validate_path("noetag/append_me.txt", no_etag_tools.valves)
        )
        await client.execute_request("upload", res_path, data=b"one\n")
        result = await no_etag_tools.append("noetag/append_me.txt", "two")
        assert result["result"] == "False"
        assert "ETag" in result["details"]
        cat = await no_etag_tools.cat("noetag/append_me.txt")
        assert cat["result"] == "True"
        assert cat["data"] == "one"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_edit_no_etag_fails_closed_preserves_content(no_etag_tools):
    from owuinc.owuinc import _webdav_path, validate_path

    client = no_etag_tools._webdav_client()
    try:
        await no_etag_tools.mkdir("noetag")
        res_path = _webdav_path(
            validate_path("noetag/edit_me.txt", no_etag_tools.valves)
        )
        await client.execute_request("upload", res_path, data=b"alpha")
        result = await no_etag_tools.edit("noetag/edit_me.txt", "alpha", "ALPHA")
        assert result["result"] == "False"
        assert "ETag" in result["details"]
        cat = await no_etag_tools.cat("noetag/edit_me.txt")
        assert cat["result"] == "True"
        assert cat["data"] == "alpha"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_append_missing_still_creates(no_etag_tools):
    await no_etag_tools.mkdir("noetag")
    result = await no_etag_tools.append("noetag/created.txt", "hello\n")
    assert result["result"] == "True"
    cat = await no_etag_tools.cat("noetag/created.txt")
    assert cat["result"] == "True"
    assert cat["data"] == "hello"
