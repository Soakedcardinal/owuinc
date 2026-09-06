# WORKFLOW
```bash
uv python install 3.11
git status && git log -1
# resolve working changes, clean state
git checkout staging && git pull && uv sync
# make changes
uv run pre-commit && uv run pytest # --cov=owuinc optional
git add -A && git commit -m "<msg>" # open a PR against main when ready
git checkout main && git pull && git checkout staging && git merge main && git push
```
