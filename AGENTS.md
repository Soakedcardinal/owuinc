# RULES
1. Only run programs and manage packages inside `.venv`.
2. Read and follow `CONTRIBUTING.md`
3. `owuinc/owuinc.py` and `startup_context_injector.py` must be single-file artifacts (no shared modules).
4. tool responses must be `{"result": "True", ...}` or `{"result": "False", "details": ...}`.
5. tool methods must be `async`

## DOCS
`README.md`
`CONTRIBUTING.md`
`LICENSE`
`docs/ext`
├── nextcloud_caldav_webdav_paths.md
├── tool-development.md
└── valves.md

## STRUCTURE
- `owuinc/owuinc.py` — OpenWebUI Nextcloud tools
- `startup_context_injector.py` — OpenWebUI filter function
- `pyproject.toml`
- `ruff.toml`
- `tests/unit/` — pure functions
- `tests/integration/` — local Radicale (CalDAV, port 5232) and WsgiDAV (WebDAV, port 5233)

## SECURITY MODEL
- Existence of the sandbox must be invisible to the agent with access to the tools. 
- `FILE_BLACKLIST` valve hides directories from all file operations.

# OpenWebui
OpenWebui Injects dicts: __event_emitter__,__event_call__,__user__ (contains the UserValves object in __user__["valves"]),__metadata__,__messages__,__files__,__model__,__oauth_token__. See `docs/ext/tool-development.md`
