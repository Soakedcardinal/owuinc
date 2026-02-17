
# owuinc: OpenWebUI → Nextcloud Integration

File, task & calendar management via Nextcloud APIs

## Structure

- owuinc/owuinc.py - main module  
- tests/ - tests
- ROADMAP.md - backlog/dev plan
- CONTRIBUTING.md - developer guide

## Architecture

Intentional single-file monolithic structure.

Tools class:
- Valves (Pydantic): config filled by OpenWebUI
- Helpers: internal utilities
- Cached webdav_client / caldav_client

## Rules

- Each Tools method must have a token efficient Sphinx-style docstring, proper type hints and return a dict
- Helper methods must go in Helpers class.

## Packaging / Distribution.
Designed for OpenWebUI community distribution (not PyPI). Module setup is only for running tests.