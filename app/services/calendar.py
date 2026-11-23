"""Calendar export (SRS §4.11): ``.ics`` + "add to Google / Outlook" links.

- ``UID`` is stable per event (``event-<id>@biletflow.kz``) so updates replace the entry.
- ``SEQUENCE`` = ``event.revision`` (bumped on every change).
- Times carry ``TZID`` of the event timezone with a matching ``VTIMEZONE``.
- A cancelled event exports ``METHOD:CANCEL`` / ``STATUS:CANCELLED`` with the same UID.
"""

from datetime import UTC, datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from icalendar import Calendar
from icalendar import Event as ICalEvent

from app.core.config import get_settings
from app.models import Event
from app.models.enums import EventStatus
from app.schemas.events import CalendarLinks


def uid(event: Event) -> str:
    return f"event-{event.id}@biletflow.kz"


def _event_url(event: Event) -> str:
    return f"{get_settings().public_web_url.rstrip('/')}/events/{event.id}"


def _location(event: Event) -> str:
    return ", ".join(p for p in (event.location_name, event.location_address) if p)


def build_ics(event: Event) -> bytes:
    tz = ZoneInfo(event.timezone)
    cancelled = event.status == EventStatus.CANCELLED

    cal = Calendar()
    cal.add("prodid", "-//BiletFlow//Event Ticketing//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "CANCEL" if cancelled else "PUBLISH")

    ev = ICalEvent()
    ev.add("uid", uid(event))
    ev.add("dtstamp", datetime.now(UTC))
    ev.add("dtstart", event.starts_at.astimezone(tz))
    ev.add("dtend", event.ends_at.astimezone(tz))
    ev.add("summary", event.title)
    ev.add("sequence", event.revision)
    ev.add("status", "CANCELLED" if cancelled else "CONFIRMED")
    ev.add("url", _event_url(event))
    if _location(event):
        ev.add("location", _location(event))
    description = (event.description or "").strip()
    ev.add("description", f"{description}\n\n{_event_url(event)}".strip())
    cal.add_component(ev)
    cal.add_missing_timezones()
    return bytes(cal.to_ical())


def links(event: Event) -> CalendarLinks:
    api = get_settings().api_base_url.rstrip("/")
    start_utc = event.starts_at.astimezone(UTC)
    end_utc = event.ends_at.astimezone(UTC)
    details = f"{(event.description or '').strip()}\n\n{_event_url(event)}".strip()
    google = "https://calendar.google.com/calendar/render?" + urlencode(
        {
            "action": "TEMPLATE",
            "text": event.title,
            "dates": f"{start_utc:%Y%m%dT%H%M%SZ}/{end_utc:%Y%m%dT%H%M%SZ}",
            "details": details,
            "location": _location(event),
            "ctz": event.timezone,
        }
    )
    outlook = "https://outlook.live.com/calendar/0/deeplink/compose?" + urlencode(
        {
            "path": "/calendar/action/compose",
            "rru": "addevent",
            "subject": event.title,
            "startdt": start_utc.isoformat(),
            "enddt": end_utc.isoformat(),
            "body": details,
            "location": _location(event),
        }
    )
    return CalendarLinks(
        ics=f"{api}/api/v1/events/{event.id}/calendar.ics", google=google, outlook=outlook
    )
