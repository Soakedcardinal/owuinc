"""
Version sync test
Ensures the artifact header versions match pyproject.toml (installed distribution)
"""

import importlib.metadata
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "owuinc" / "owuinc.py"
INJECTOR_PATH = ROOT / "startup_context_injector.py"


def _header_version(path: Path) -> str:
    header = path.read_text().split('"""', 2)[1]
    match = re.search(r"^version:\s*(\S+)", header, re.MULTILINE)
    assert match, f"no version field found in {path.name} module header"
    return match.group(1)


class TestVersionSync:
    """artifact header versions and pyproject.toml version must stay equal"""

    def test_module_header_matches_distribution(self):
        assert _header_version(MODULE_PATH) == importlib.metadata.version("owuinc")

    def test_injector_header_matches_module(self):
        assert _header_version(INJECTOR_PATH) == _header_version(MODULE_PATH)
