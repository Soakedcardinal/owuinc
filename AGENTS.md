# GIT

- Session start: `git fetch origin`, check `git log --oneline @{u}..HEAD` is empty, then `git merge --ff-only @{u}`
- Commit as you go; **push to `origin` immediately after every commit**

# RULES

1. Only run programs and manage packages inside `.venv`.
2. Read and follow `CONTRIBUTING.md`
3. `owuinc/owuinc.py` and `startup_context_injector.py` must be single-file artifacts (no shared modules).
4. tool responses must be `{"result": "True", ...}` or `{"result": "False", "details": ...}`.
5. tool methods must be `async`

# DOCS

`README.md`
`CONTRIBUTING.md`
`LICENSE`
`docs/ext`
├── nextcloud_caldav_webdav_paths.md
├── tool-development.md
└── valves.md

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
  deployed OpenWebUI release (currently tag `v0.11.4`). Reference only: read it to answer
  integration questions (injection points, `backend/requirements.txt` pins, packaging and
  update behavior), and as the basis for future full-stack integration tests against a
  real OpenWebUI + Nextcloud test server. It is not a dependency of this project — never
  build, import, or install from it.
- Bump the pin when the deployed OpenWebUI version changes:
  ```bash
  git -C third_party/open-webui fetch --tags
  git -C third_party/open-webui checkout v<NEW>
  git add third_party/open-webui && git commit -m "ref: pin open-webui v<NEW>"
  ```
- Fresh clones: `git submodule update --init`.

# SECURITY MODEL

- Existence of the sandbox must be invisible to the agent with access to the tools. 
- `FILE_BLACKLIST` valve hides directories from all file operations.

# OpenWebui

OpenWebui Injects dicts: __event_emitter__,__event_call__,__user__ (contains the UserValves object in __user__["valves"]),__metadata__,__messages__,__files__,__model__,__oauth_token__. See `docs/ext/tool-development.md`

# STYLE

Be terse.
