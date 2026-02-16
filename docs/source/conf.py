# Configuration file for the Sphinx documentation builder.
import os
import sys

# Add parent directory to path so we can import owuinc
sys.path.insert(0, os.path.abspath("../.."))

project = "owuinc"
copyright = "2026, Duncan Nicholson"
author = "Duncan Nicholson"
release = "1.0.3"
extensions = ["sphinx.ext.autodoc"]
exclude_patterns: list[str] = []
html_theme = "sphinx_rtd_theme"

# Autodoc configuration
autodoc_member_order = "bysource"
autodoc_default_flags = ["members", "inherited-members"]
autodoc_default_options = {
    "members": True,
    "member-order": "bysource",
    "special-members": "__init__",
    "undoc-members": True,
    "show-inheritance": True,
}
