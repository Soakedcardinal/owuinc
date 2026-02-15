# Contributing to owuinc

Run the following commands to initialize the environment:

```bash
rm -r .venv .pytest_cache .mypy_cache __pycache__ docs/build
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
pip install -e .
pre-commit install
pre-commit run --all-files
pytest
sphinx-build -M html docs/source/ docs/build/
```