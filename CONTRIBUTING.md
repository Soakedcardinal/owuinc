# Contributing to owuinc

## 1. only if needed/first run: create new venv 
```bash
rm -r .venv .pytest_cache .mypy_cache __pycache__ 
python3 -m venv .venv
```

## 2. activate venv
```bash
source .venv/bin/activate
```

## 3. deps
```bash
pip install -U pip
pip install -r requirements.txt -r requirements-dev.txt
pip install -e .
```

## 4. hooks
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