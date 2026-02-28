


### **Phase 1: Stability & Critical Fixes**
*   [ ] create cal/task lists / sandbox dir automatically if they don't exist
*   [ ] move from a single sandbox to file/directory whitelist?
*   [ ] Fix intermittent bug: no url valve when using for multiple instances of tool (?) -> Validate base URL?
*   [ ] Add fallback timezone if __user__["timezone"] not set (e.g. utc)
*   [ ] Audit test_helpers.py
*   [ ] Implement proper input validation beyond path validation.

### **Phase 2: Core Functionality & Data Integrity**
*Focus: Improving the logic for handling events, tasks, and Nextcloud-specific data.*

*   **Event & Task Management**
    *   [ ] **UID Support:** Accept UID for all task/event methods.
    *   [ ] **Cross-List Lookup:** If passing a UID, identify the event/task regardless of which calendar or task list it resides on.
    *   [ ] **Bulk Deletion:** Modify `delete_calendar_event` to accept a list `event_summaries[]`.
    *   [ ] **Multiple Matches:** Ensure `delete_calendar_event` handles multiple summary matches without expanding `rrule`.
    *   [ ] **Whitelisting:** Implement whitelist functionality for task lists.
    *   [ ] **Calendar Validation:** Add `is_valid_cal(self)` to validate specified calendar/list name args.
*   **Date & String Handling**
    *   [ ] **All-Day Date Fix:** Handle the iCalendar edge case where `dtend` is a `date` (not datetime). Subtract one day from the end date to display correctly (e.g., Jan 1 - Jan 3, not Jan 1 - Jan 4).
    *   [ ] **URL Encoding:** Use `urllib.parse.unquote()` to decode filenames with spaces or special characters before displaying them to the user.

### **Phase 3: Testing & Code Quality**
*Focus: Making the codebase robust and maintainable.*

*   **Testing Infrastructure**
    *   [ ] Set up a local test server (Radicale) for CalDAV testing.
    *   [ ] Improve existing tests for tasks and calendar events.
    *   [ ] Add code coverage reporting (`pytest-cov`).
*   **Static Analysis & Complexity**
    *   [ ] Improve type hints for better tool schema parsing and static analysis

### **Phase 4: Configuration & Architecture**
*Focus: Structuring the project for future scale.*

*   **Configuration Management**
    *   [ ] Add `pydantic-settings` for config validation.
    *   [ ] Add configuration validation at startup.
    *   [ ] Fix up and use proper semantic versioning.
*   **Search & Metadata**
    *   [ ] Implement glob and/or tool (search) functionality.
    *   [ ] **Nextcloud Metadata:** Manually query Nextcloud-specific XML properties (`oc:fileid`, `oc:size`) which the generic library might ignore.

### **Phase 5: Future Enhancements**
*Focus: Long-term roadmap items.*

*   **Code Design**
    *   [ ] Design a code generation system to split the monolithic file into separate modules.
*   **Security & Auth**
    *   [ ] Add OAuth support.