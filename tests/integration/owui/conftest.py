"""Integration fixtures for plugin tests: a real Open WebUI + a mock OpenAI upstream.

How the server is sourced (first available wins):

1. ``OWUI_URL`` — an already-running instance. Its OpenAI connection must point at the
   mock LLM (http://localhost:9999/v1 by default); creds via OWUI_EMAIL / OWUI_PASSWORD.
2. ``open-webui`` installed in the current environment (python <3.13) — started here on
   port 5234 with a throwaway DATA_DIR and the mock as its only provider.
3. ``docker`` — ``docker compose up`` in this directory, torn down after the session.
4. none of the above — tests skip.
"""

import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from .mock_llm import MockLLM
from .owui import OWUI

HERE = Path(__file__).parent
EMAIL = os.getenv("OWUI_EMAIL", "admin@test.local")
PASSWORD = os.getenv("OWUI_PASSWORD", "admin-password")
PORT = 5234  # the pip build defaults to 8080; ours stays distinct, like 5232/5233


def _find_open_webui_cli() -> str | None:
    exe = shutil.which("open-webui")
    if exe:
        return exe
    candidate = Path(sys.executable).parent / "open-webui"
    return str(candidate) if candidate.exists() else None


def _port_open(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()


@pytest.fixture(scope="session")
def mock_llm():
    mock = MockLLM(port=int(os.getenv("MOCK_LLM_PORT", "9999")))
    mock.start()
    yield mock
    mock.stop()


@pytest.fixture(scope="session")
def owui(mock_llm):
    """A signed-in client for an Open WebUI whose only model is the mock."""
    url = os.getenv("OWUI_URL")
    server = None
    logfile = None
    data_dir = None
    compose = False
    try:
        # the client needs httpx; skip rather than error if it is missing
        pytest.importorskip("httpx", reason="httpx not installed — pip install httpx")
        if url is None:
            cli = _find_open_webui_cli()
            if cli is None:
                if shutil.which("docker"):
                    subprocess.run(
                        ["docker", "compose", "up", "-d"], cwd=HERE, check=True
                    )
                    compose = True
                    url = "http://localhost:3000"
                else:
                    pytest.skip(
                        "no Open WebUI available: set OWUI_URL, install open-webui "
                        "(python <3.13), or install docker"
                    )
            elif _port_open(PORT):
                pytest.skip(
                    f"port {PORT} busy — set OWUI_URL to reuse the running instance"
                )
            else:
                data_dir = Path(tempfile.mkdtemp(prefix="owui_test_"))
                logfile = (data_dir / "server.log").open("w")
                server = subprocess.Popen(
                    [cli, "serve", "--port", str(PORT)],
                    env=os.environ
                    | {
                        "DATA_DIR": str(data_dir),
                        "WEBUI_SECRET_KEY": "test-secret",
                        "WEBUI_ADMIN_EMAIL": EMAIL,
                        "WEBUI_ADMIN_PASSWORD": PASSWORD,
                        "ENABLE_OLLAMA_API": "false",
                        "ENABLE_BASE_MODELS_CACHE": "false",
                        "OPENAI_API_BASE_URL": f"http://127.0.0.1:{mock_llm.port}/v1",
                        "OPENAI_API_KEY": "dummy",
                    },
                    stdout=logfile,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                url = f"http://127.0.0.1:{PORT}"
        assert url is not None
        client = OWUI(url, EMAIL, PASSWORD)
        client.wait_ready()
        yield client
    finally:
        if server is not None:
            try:
                os.killpg(os.getpgid(server.pid), signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(server.pid), signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass
                server.wait()
        if logfile is not None:
            logfile.close()
        if data_dir is not None:
            shutil.rmtree(data_dir, ignore_errors=True)
        if compose:
            subprocess.run(["docker", "compose", "down", "-v"], cwd=HERE, check=False)


@pytest.fixture(autouse=True)
def _fresh_mock(mock_llm):
    mock_llm.reset()


@pytest.fixture
def install_filter(owui):
    """install_filter("plugins/x.py") -> function id. Filters are installed as
    global+active, so they are switched off again after each test."""
    ids: list[str] = []

    def _install(path: str) -> str:
        fid = owui.install_function(HERE / path)
        ids.append(fid)
        return fid

    yield _install
    for fid in ids:
        owui.set_function_flags(fid, active=False, glob=False)


@pytest.fixture
def install_tool(owui):
    """install_tool("plugins/x.py") -> tool id. Tools only run when a request
    lists them in tool_ids, so no cleanup is needed."""
    return lambda path: owui.install_tool(HERE / path)
