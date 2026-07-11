# Contributing to owuinc

## Create new venv 
Clean up if needed
```bash
rm -r .venv .pytest_cache .mypy_cache __pycache__ cz
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
pytest
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

clean up
```bash
rm -rf __pycache__ .mypy_cache .pytest_cache .venv htmlcov owuinc/__pycache__ owuinc.egg-info tests/__pycache__ .coverage || true
```