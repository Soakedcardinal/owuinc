# Contributing to owuinc

## 1. first run only: create new venv 
clean up if needed
```bash
rm -r .venv .pytest_cache .mypy_cache __pycache__ || true
python3 -m venv .venv
```

create venv
```bash
python3 -m venv .venv
```

activate venv
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

Install main module
```bash
pip install -e .
```


# Workflow
```bash
pre-commit install
pre-commit run --all-files
```

## 5. tests
```bash
pytest
```

## 6. docs
```bash
rm -r docs/build
sphinx-build -M html docs/source/ docs/build/
```