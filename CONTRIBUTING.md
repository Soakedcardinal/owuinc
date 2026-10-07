```bash
git clone --recurse-submodules git@github.com:Soakedcardinal/owuinc.git && cd owuinc
git status && git log -1

# work on staging
git checkout staging && git pull && git submodule update --init && uv sync
uv run pre-commit install   # lint hooks

git add foo && uv run pre-commit && uv run pytest   # --cov=owuinc optional
git commit -m "bar"
# open a PR against main
```