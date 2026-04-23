# Contributing to owuinc

## Create new venv 
Clean up if needed
```bash
rm -r .venv .pytest_cache .mypy_cache __pycache__ || true
```

Create venv
```bash
python3 -m venv .venv
```

Activate venv
```bash
# bash
source .venv/bin/activate

# fish
source .venv/bin/activate.fish
```

Update pip
```bash
pip install -U pip
```

Install deps
```bash
pip install -r requirements.txt -r requirements-dev.txt
```

Install `owuinc` module
```bash
pip install -e .
```

## Workflow

Run hooks
```bash
pre-commit install
pre-commit run --all-files
```

Run tests
```bash
# All non-E2E tests (unit + integration) - default for CI and development
pytest

# Unit tests only (pure functions, no external dependencies)
pytest tests/unit/ -v

# Integration tests (local CalDAV/WebDAV servers)
pytest tests/integration/ -v

# E2E tests only (requires live OpenWebUI + Nextcloud - manual release checks ONLY)
# Must set env vars: URL, KEY, USER_ID, FOLDER_ID
pytest tests/e2e/ -v

# Specific test file
pytest tests/unit/test_helpers.py -v
pytest tests/integration/test_calendar_methods.py -v
```

Coverage
```bash
pytest --cov=owuinc
```

Build API doc
```bash
rm -r docs/build
sphinx-build -M html docs/source/ docs/build/
```
