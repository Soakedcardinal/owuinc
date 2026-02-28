# Contributing to owuinc


```bash
# create new venv
rm -r .venv .pytest_cache .mypy_cache __pycache__ 
python3 -m venv .venv

# activate venv
source .venv/bin/activate

# deps
pip install -U pip
pip install -r requirements.txt -r requirements-dev.txt
pip install -e .

# hooks
pre-commit install
pre-commit run --all-files

# tests
pytest

# docs
rm -r docs/build
sphinx-build -M html docs/source/ docs/build/
```