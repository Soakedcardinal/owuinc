# GIT

- Session start: `git fetch origin`, check `git log --oneline @{u}..HEAD` is empty, then `git merge --ff-only @{u}`
- Commit as you go; push to `origin` immediately after every commit

# RULES

1. Only run programs and manage packages inside `.venv`.
2. Read and follow `CONTRIBUTING.md`
3. `owuinc/owuinc.py` and `startup_context_injector.py` must be single-file artifacts (no shared modules).
4. tool responses must be `{"result": "True", ...}` or `{"result": "False", "details": ...}`.
5. tool methods must be `async`
6. Be terse.

# STRUCTURE

- `owuinc/owuinc.py` — OpenWebUI Nextcloud tools
- `startup_context_injector.py` — OpenWebUI filter function
- `pyproject.toml`
- `ruff.toml`
- `tests/unit/` — pure functions
- `tests/integration/` — local Radicale (CalDAV, port 5232) and WsgiDAV (WebDAV, port 5233)
- `tests/integration/owui/` — OpenWebUI plugin integration tests (real server on :5234 + mock LLM on :9999; see its README)
- `third_party/open-webui/` — git submodule; OpenWebUI source pinned to the release under test

# EXTERNAL REFERENCES

- `third_party/open-webui/` — full-history clone of open-webui/open-webui, pinned to the
  latest OpenWebUI release. Reference only.
- Bump the pin after OpenWebUI releases.

# SECURITY MODEL

- Existence of the sandbox must be invisible to the agent with access to the tools. 
- `FILE_BLACKLIST` valve hides directories from all file operations.

# OpenWebui

OpenWebui Injects dicts: __event_emitter__,__event_call__,__user__ (contains the UserValves object in __user__["valves"]),__metadata__,__messages__,__files__,__model__,__oauth_token__. See `docs/ext/tool-development.md`
