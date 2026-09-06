"""
Version sync test
Ensures the owuinc module header version matches pyproject.toml (installed distribution)
"""

import importlib.metadata
import re
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "owuinc" / "owuinc.py"


def _header_version() -> str:
    header = MODULE_PATH.read_text().split('"""', 2)[1]
    match = re.search(r"^version:\s*(\S+)", header, re.MULTILINE)
    assert match, "no version field found in owuinc module header"
    return match.group(1)


class TestVersionSync:
    """owuinc.py header and pyproject.toml version must stay equal"""

    def test_module_header_matches_distribution(self):
        assert _header_version() == importlib.metadata.version("owuinc")
