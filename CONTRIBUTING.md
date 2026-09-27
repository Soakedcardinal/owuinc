# WORKFLOW
```bash
# fresh clone only (pulls in the third_party/open-webui submodule):
git clone --recurse-submodules <repo-url> && cd owuinc

uv python install 3.11
git status && git log -1

# resolve working changes, clean state. work on staging
git checkout staging && git pull && git submodule update --init && uv sync
uv run pre-commit install   # run the lint hooks on every commit (once per clone)

# make changes

# stage first: pre-commit checks the STAGED files (staging nothing = silent no-op)
git add <files> && uv run pre-commit && uv run pytest # --cov=owuinc optional
git commit -m "<msg>"
# open a PR against main when ready
```

# SUBMODULE
`third_party/open-webui` — OpenWebUI source pinned to the release under test; reference only
(see AGENTS.md, EXTERNAL REFERENCES).
- after a `git pull` that may have moved the pin: `git submodule update --init` (already in the
  workflow above).
- bumping the pin: `git -C third_party/open-webui fetch --tags` then
  `git -C third_party/open-webui checkout v<NEW>`, then `git add third_party/open-webui` and
  commit the gitlink.
- never commit inside the submodule; only the gitlink in this repo is the change.
