"""Unit tests for calendar events JSON serialization."""

import json


class TestCalendarEventsSerialization:
    def test_icalendar_property_serialized_to_str(self):
        """Ensure icalendar property objects are stringified."""
        from icalendar import Event

        e = Event()
        e.add("summary", "Test Event")
        e.add("description", "A test description")
        e.add("location", "Office")

        component = e
        event_dict = {}
        for field in ["summary", "description", "location"]:
            if val := component.get(field):
                event_dict[field] = str(val)

        # Should be JSON-serializable
        json.dumps(event_dict)
        assert event_dict["summary"] == "Test Event"
        assert event_dict["description"] == "A test description"
        assert event_dict["location"] == "Office"

    def test_categories_serialized_as_list(self):
        """Ensure categories are converted to list of strings."""
        from icalendar import Event

        e = Event()
        e.add("categories", ["Work", "Meeting"])

        component = e
        event_dict = {}
        if val := component.get("categories"):
            event_dict["categories"] = [str(c) for c in val.cats]

        json.dumps(event_dict)
        assert event_dict["categories"] == ["Work", "Meeting"]

    def test_full_event_roundtrip(self):
        """Full event dict round-trips through json.dumps/load."""
        from icalendar import Event

        e = Event()
        e.add("summary", "Team Standup")
        e.add("description", "Daily standup")
        e.add("location", "Room A")
        e.add("categories", ["Work", "Recurring"])
        e.add("organizer", "mailto:admin@example.com")
        e.add("url", "https://cal.example.com/e/123")

        event_dict = {}
        for field in [
            "summary",
            "description",
            "location",
            "categories",
            "organizer",
            "url",
        ]:
            if val := (
                e.component.get(field) if hasattr(e, "component") else e.get(field)
            ):
                if field == "categories":
                    event_dict[field] = [str(c) for c in val.cats]
                else:
                    event_dict[field] = str(val)

        serialized = json.dumps(event_dict)
        deserialized = json.loads(serialized)
        assert deserialized["summary"] == "Team Standup"
        assert deserialized["categories"] == ["Work", "Recurring"]
        assert deserialized["organizer"] == "mailto:admin@example.com"
        assert deserialized["url"] == "https://cal.example.com/e/123"
