# Contributing to owuinc

## Create new venv 
Clean up if needed
```bash
rm -r .venv .pytest_cache .mypy_cache __pycache__ || true
python3 -m venv .venv
```

Create venv
```bash
python3 -m venv .venv
```

Activate venv
```bash
source .venv/bin/activate
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
pytest
# or individually:
pytest tests/test_helpers.py -v
pytest tests/test_owuinc.py -v
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
