"""Integration tests - local integration with mock/backed servers.

This module contains tests that integrate with real services running locally:
- CalDAV: Radicale server for calendar/task operations
- WebDAV: TBD - separate test server for file operations

These tests are fast (seconds) and CI-friendly, unlike E2E tests which require
full production stack (OpenWebUI + Nextcloud).
"""
