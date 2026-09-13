# WORKFLOW
```bash
uv python install 3.11
git status && git log -1

# resolve working changes, clean state. work on staging
git checkout staging && git pull && uv sync

# make changes

uv run pre-commit && uv run pytest # --cov=owuinc optional

git add <files> && git commit -m "<msg>"
# open a PR against main when ready
```
