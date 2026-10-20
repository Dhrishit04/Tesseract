"""Google Calendar skills for Tesseract - list, create, update, delete events and check schedule."""

import logging
from datetime import datetime, timedelta
from typing import Any

from skills.skill_base import BlockingSkill, SkillResult

logger = logging.getLogger(__name__)


def _get_calendar_service():
    """Get authenticated Calendar service from OAuth manager."""
    from auth.google_oauth import get_google_oauth_manager
    oauth = get_google_oauth_manager()
    return oauth.get_calendar_service()


def _format_event(event: dict) -> dict:
    """Format a Calendar event for display."""
    start = event.get("start", {})
    end = event.get("end", {})

    # Handle both dateTime and date formats
    start_str = start.get("dateTime", start.get("date", ""))
    end_str = end.get("dateTime", end.get("date", ""))

    return {
        "id": event.get("id"),
        "summary": event.get("summary", "(No Title)"),
        "description": event.get("description", ""),
        "location": event.get("location", ""),
        "start": start_str,
        "end": end_str,
        "timezone": start.get("timeZone", end.get("timeZone", "")),
        "attendees": [
            {"email": a.get("email"), "response": a.get("responseStatus", "needsAction")}
            for a in event.get("attendees", [])
        ],
        "creator": event.get("creator", {}).get("email", ""),
        "organizer": event.get("organizer", {}).get("email", ""),
        "status": event.get("status", ""),
        "html_link": event.get("htmlLink", ""),
        "recurring_event_id": event.get("recurringEventId", ""),
    }


def _when(date_time: str, time_zone: str | None) -> dict:
    """Event time for the API. With no zone, Google rejects a time lacking a UTC offset instead of guessing."""
    return {"dateTime": date_time, "timeZone": time_zone} if time_zone else {"dateTime": date_time}


def _parse_datetime(dt_str: str) -> datetime:
    """Parse ISO datetime string to datetime object."""
    # Handle both dateTime (with timezone) and date (all-day)
    if "T" in dt_str:
        # Remove Z and parse
        if dt_str.endswith("Z"):
            dt_str = dt_str[:-1] + "+00:00"
        return datetime.fromisoformat(dt_str)
    else:
        return datetime.fromisoformat(dt_str + "T00:00:00")


class CalendarListEventsSkill(BlockingSkill):
    """Skill to list upcoming calendar events."""

    @property
    def name(self) -> str:
        return "calendar.list_events"

    @property
    def description(self) -> str:
        return "List upcoming Google Calendar events. Can filter by time range and max results."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "calendar_id": {
                    "type": "string",
                    "description": "Calendar ID (default: 'primary')",
                    "default": "primary",
                },
                "time_min": {
                    "type": "string",
                    "description": "Start time in ISO format (default: now)",
                },
                "time_max": {
                    "type": "string",
                    "description": "End time in ISO format (default: 7 days from now)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum events to return (default: 20, max: 100)",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 100,
                },
                "single_events": {
                    "type": "boolean",
                    "description": "Expand recurring events (default: true)",
                    "default": True,
                },
                "order_by": {
                    "type": "string",
                    "description": "Order events by 'startTime' or 'updated'",
                    "enum": ["startTime", "updated"],
                    "default": "startTime",
                },
            },
        }

    def run(self, **kwargs: Any) -> SkillResult:
        calendar_id = kwargs.get("calendar_id", "primary")
        time_min = kwargs.get("time_min")
        time_max = kwargs.get("time_max")
        max_results = kwargs.get("max_results", 20)
        single_events = kwargs.get("single_events", True)
        order_by = kwargs.get("order_by", "startTime")

        # Default time range
        if not time_min:
            time_min = datetime.utcnow().isoformat() + "Z"
        if not time_max:
            time_max = (datetime.utcnow() + timedelta(days=7)).isoformat() + "Z"

        try:
            service = _get_calendar_service()

            events_result = service.events().list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                maxResults=max_results,
                singleEvents=single_events,
                orderBy=order_by,
            ).execute()

            events = events_result.get("items", [])
            formatted = [_format_event(e) for e in events]

            return SkillResult.success({
                "events": formatted,
                "count": len(formatted),
                "time_min": time_min,
                "time_max": time_max,
            })
        except Exception as e:
            logger.exception("Error listing events")
            return SkillResult.failure(f"Error listing events: {e}")


class CalendarCreateEventSkill(BlockingSkill):
    """Skill to create a new calendar event."""

    @property
    def name(self) -> str:
        return "calendar.create_event"

    @property
    def description(self) -> str:
        return "Create a new Google Calendar event."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "calendar_id": {
                    "type": "string",
                    "description": "Calendar ID (default: 'primary')",
                    "default": "primary",
                },
                "summary": {
                    "type": "string",
                    "description": "Event title/summary",
                },
                "description": {
                    "type": "string",
                    "description": "Event description",
                    "default": "",
                },
                "location": {
                    "type": "string",
                    "description": "Event location",
                    "default": "",
                },
                "start_time": {
                    "type": "string",
                    "description": "Start time in ISO format (e.g., '2024-01-15T10:00:00' or '2024-01-15T10:00:00-05:00')",
                },
                "end_time": {
                    "type": "string",
                    "description": "End time in ISO format",
                },
                "timezone": {
                    "type": "string",
                    "description": "IANA time zone (e.g., 'America/New_York'). Not needed when start_time and end_time include a UTC offset.",
                },
                "attendees": {
                    "type": "array",
                    "description": "List of attendee emails",
                    "items": {"type": "string"},
                    "default": [],
                },
                "send_notifications": {
                    "type": "boolean",
                    "description": "Send email notifications to attendees",
                    "default": True,
                },
            },
            "required": ["summary", "start_time", "end_time"],
        }

    def run(self, **kwargs: Any) -> SkillResult:
        calendar_id = kwargs.get("calendar_id", "primary")
        summary = kwargs.get("summary", "")
        description = kwargs.get("description", "")
        location = kwargs.get("location", "")
        start_time = kwargs.get("start_time", "")
        end_time = kwargs.get("end_time", "")
        timezone = kwargs.get("timezone")
        attendees = kwargs.get("attendees", [])
        send_notifications = kwargs.get("send_notifications", True)

        if not summary or not start_time or not end_time:
            return SkillResult.failure("summary, start_time, and end_time are required")

        try:
            service = _get_calendar_service()

            # Build event
            event = {
                "summary": summary,
                "description": description,
                "location": location,
                "start": _when(start_time, timezone),
                "end": _when(end_time, timezone),
            }

            if attendees:
                event["attendees"] = [{"email": email} for email in attendees]

            created = service.events().insert(
                calendarId=calendar_id,
                body=event,
                sendUpdates="all" if send_notifications else "none",
            ).execute()

            return SkillResult.success({
                "event": _format_event(created),
                "status": "created",
            })
        except Exception as e:
            logger.exception("Error creating event")
            return SkillResult.failure(f"Error creating event: {e}")


class CalendarUpdateEventSkill(BlockingSkill):
    """Skill to update an existing calendar event."""

    @property
    def name(self) -> str:
        return "calendar.update_event"

    @property
    def description(self) -> str:
        return "Update an existing Google Calendar event. Only provided fields are updated."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "calendar_id": {
                    "type": "string",
                    "description": "Calendar ID (default: 'primary')",
                    "default": "primary",
                },
                "event_id": {
                    "type": "string",
                    "description": "ID of the event to update",
                },
                "summary": {
                    "type": "string",
                    "description": "New event title",
                },
                "description": {
                    "type": "string",
                    "description": "New event description",
                },
                "location": {
                    "type": "string",
                    "description": "New event location",
                },
                "start_time": {
                    "type": "string",
                    "description": "New start time in ISO format",
                },
                "end_time": {
                    "type": "string",
                    "description": "New end time in ISO format",
                },
                "timezone": {
                    "type": "string",
                    "description": "New timezone",
                },
                "attendees": {
                    "type": "array",
                    "description": "New attendee list (replaces existing)",
                    "items": {"type": "string"},
                },
                "send_notifications": {
                    "type": "boolean",
                    "description": "Send email notifications to attendees",
                    "default": True,
                },
            },
            "required": ["event_id"],
        }

    def run(self, **kwargs: Any) -> SkillResult:
        calendar_id = kwargs.get("calendar_id", "primary")
        event_id = kwargs.get("event_id", "")
        send_notifications = kwargs.get("send_notifications", True)

        if not event_id:
            return SkillResult.failure("event_id is required")

        try:
            service = _get_calendar_service()

            # Get existing event
            existing = service.events().get(calendarId=calendar_id, eventId=event_id).execute()

            # Update only provided fields
            if "summary" in kwargs and kwargs["summary"]:
                existing["summary"] = kwargs["summary"]
            if "description" in kwargs and kwargs["description"] is not None:
                existing["description"] = kwargs["description"]
            if "location" in kwargs and kwargs["location"] is not None:
                existing["location"] = kwargs["location"]
            if "start_time" in kwargs and kwargs["start_time"]:
                existing["start"] = _when(
                    kwargs["start_time"], kwargs.get("timezone") or existing.get("start", {}).get("timeZone")
                )
            if "end_time" in kwargs and kwargs["end_time"]:
                existing["end"] = _when(
                    kwargs["end_time"], kwargs.get("timezone") or existing.get("end", {}).get("timeZone")
                )
            if "attendees" in kwargs:
                existing["attendees"] = [{"email": email} for email in kwargs["attendees"]]

            updated = service.events().update(
                calendarId=calendar_id,
                eventId=event_id,
                body=existing,
                sendUpdates="all" if send_notifications else "none",
            ).execute()

            return SkillResult.success({
                "event": _format_event(updated),
                "status": "updated",
            })
        except Exception as e:
            logger.exception("Error updating event")
            return SkillResult.failure(f"Error updating event: {e}")


class CalendarDeleteEventSkill(BlockingSkill):
    """Skill to delete a calendar event."""

    @property
    def name(self) -> str:
        return "calendar.delete_event"

    @property
    def description(self) -> str:
        return "Delete a Google Calendar event."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "calendar_id": {
                    "type": "string",
                    "description": "Calendar ID (default: 'primary')",
                    "default": "primary",
                },
                "event_id": {
                    "type": "string",
                    "description": "ID of the event to delete",
                },
                "send_notifications": {
                    "type": "boolean",
                    "description": "Send cancellation emails to attendees",
                    "default": True,
                },
            },
            "required": ["event_id"],
        }

    def run(self, **kwargs: Any) -> SkillResult:
        calendar_id = kwargs.get("calendar_id", "primary")
        event_id = kwargs.get("event_id", "")
        send_notifications = kwargs.get("send_notifications", True)

        if not event_id:
            return SkillResult.failure("event_id is required")

        try:
            service = _get_calendar_service()

            service.events().delete(
                calendarId=calendar_id,
                eventId=event_id,
                sendUpdates="all" if send_notifications else "none",
            ).execute()

            return SkillResult.success({
                "event_id": event_id,
                "status": "deleted",
            })
        except Exception as e:
            logger.exception("Error deleting event")
            return SkillResult.failure(f"Error deleting event: {e}")


class CalendarCheckScheduleSkill(BlockingSkill):
    """Skill to check calendar availability (free/busy)."""

    @property
    def name(self) -> str:
        return "calendar.check_schedule"

    @property
    def description(self) -> str:
        return "Check free/busy schedule for one or more calendars in a time range."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "calendar_ids": {
                    "type": "array",
                    "description": "Calendar IDs to check (default: ['primary'])",
                    "items": {"type": "string"},
                    "default": ["primary"],
                },
                "time_min": {
                    "type": "string",
                    "description": "Start time in ISO format (default: now)",
                },
                "time_max": {
                    "type": "string",
                    "description": "End time in ISO format (default: 24 hours from now)",
                },
                "timezone": {
                    "type": "string",
                    "description": "Timezone for results",
                    "default": "UTC",
                },
            },
        }

    def run(self, **kwargs: Any) -> SkillResult:
        calendar_ids = kwargs.get("calendar_ids", ["primary"])
        time_min = kwargs.get("time_min")
        time_max = kwargs.get("time_max")
        timezone = kwargs.get("timezone", "UTC")

        # Default time range: next 24 hours
        if not time_min:
            time_min = datetime.utcnow().isoformat() + "Z"
        if not time_max:
            time_max = (datetime.utcnow() + timedelta(hours=24)).isoformat() + "Z"

        try:
            service = _get_calendar_service()

            body = {
                "timeMin": time_min,
                "timeMax": time_max,
                "timeZone": timezone,
                "items": [{"id": cid} for cid in calendar_ids],
            }

            freebusy = service.freebusy().query(body=body).execute()

            calendars = freebusy.get("calendars", {})
            result = {}
            for cal_id, cal_data in calendars.items():
                busy = cal_data.get("busy", [])
                formatted_busy = [
                    {
                        "start": b.get("start"),
                        "end": b.get("end"),
                    }
                    for b in busy
                ]
                result[cal_id] = {
                    "busy": formatted_busy,
                    "errors": cal_data.get("errors", []),
                }

            return SkillResult.success({
                "calendars": result,
                "time_min": time_min,
                "time_max": time_max,
            })
        except Exception as e:
            logger.exception("Error checking schedule")
            return SkillResult.failure(f"Error checking schedule: {e}")