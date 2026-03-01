# Roadmap

## Immediate Priorities
- [ ] factory for default calendar / task list for accurate tool schema - remove from system prompt
- [ ] handle mixed vevent/todo
- [ ] Create cal/task lists / sandbox dir automatically if they don't exist
- [ ] Move from single sandbox to file/directory whitelist
- [ ] Add fallback timezone if `__user__["timezone"]` not set (e.g. utc)
- [ ] Audit test_helpers.py
- [ ] Implement proper input validation beyond path validation
- [ ] Render API docs

## Event & Task Management
- [ ] Accept UID for all task/event methods
- [ ] Cross-list lookup: identify event/task regardless of calendar or task list
- [ ] Bulk deletion: modify `delete_calendar_event` to accept list `event_summaries[]`
- [ ] Handle multiple matches in `delete_calendar_event` without expanding `rrule`


## Date & String Handling
- [ ] Fix all-day date edge case: subtract one day from `dtend` when it's a date (not datetime)
- [ ] Use `urllib.parse.unquote()` to decode filenames with spaces/special characters

## Testing & Quality
- [ ] Set up local test server (Radicale) for CalDAV testing
- [ ] Improve existing tests for tasks and calendar events
- [ ] Add code coverage reporting (`pytest-cov`)
- [ ] Improve type hints for better tool schema parsing

## Configuration
- [ ] Add `pydantic-settings` for config validation
- [ ] Add configuration validation at startup
- [ ] Fix up and use proper semantic versioning

## Search & Metadata
- [ ] Implement glob and/or tool (search) functionality
- [ ] Query Nextcloud-specific XML properties (`oc:fileid`, `oc:size`)

## Future Enhancements
- [ ] Design code generation system to split monolithic file into separate modules
- [ ] Add OAuth support