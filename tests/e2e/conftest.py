"""E2E tests require environment variables - skip entire module if not set."""

import os

import pytest

# Check if required env vars exist at collection time
_REQUIRED_ENV_VARS = ["URL", "KEY", "USER_ID", "FOLDER_ID"]
_MISSING = [v for v in _REQUIRED_ENV_VARS if not os.getenv(v)]


def pytest_collection_modifyitems(config, items):
    """Skip all E2E tests if required environment variables are missing."""
    if _MISSING:
        skip_marker = pytest.mark.skip(
            reason=f"E2E tests require environment variables: {_MISSING}"
        )
        for item in items:
            item.add_marker(skip_marker)
