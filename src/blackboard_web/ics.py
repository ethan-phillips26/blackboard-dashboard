"""Turn cached due dates into an iCalendar (RFC 5545) feed.

The dashboard's calendar is not drawn from the JSON API — it renders whatever is
in this feed, so the grid on screen and the file you import into Google Calendar
or Apple Calendar are guaranteed to agree. Anything wrong in one is wrong in the
other, which is the point.

Times are emitted in UTC (`...Z`). A due date is an instant, so there is nothing
to gain from a VTIMEZONE block, and every client agrees on what a `Z` means.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

PRODID = "-//blackboard-dashboard//Whiteboard//EN"
UID_DOMAIN = "blackboard-dashboard.local"

# How long a deadline block occupies on the calendar. A due date is an instant;
# a short block ending at the deadline is how every calendar UI reads it.
BLOCK_MINUTES = 30


def _esc(value: Any) -> str:
    """Escape a value for a TEXT property (RFC 5545 §3.3.11)."""
    text = "" if value is None else str(value)
    text = text.replace("\\", "\\\\")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def _fold(line: str) -> list[str]:
    """Split one content line into 75-octet chunks, continuations space-prefixed.

    The limit is on octets, not characters, so the split walks encoded bytes and
    never lands inside a multi-byte character.
    """
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return [line]
    out: list[str] = []
    start, limit = 0, 75
    while start < len(raw):
        end = min(start + limit, len(raw))
        # Back off until the chunk ends on a character boundary.
        while end > start and end < len(raw) and (raw[end] & 0xC0) == 0x80:
            end -= 1
        chunk = raw[start:end].decode("utf-8")
        out.append(chunk if not out else " " + chunk)
        start = end
        limit = 74  # continuation lines spend one octet on the leading space
    return out


def _utc(value: Any) -> str | None:
    """Format an ISO timestamp (or datetime) as a UTC iCalendar stamp."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _uid(item: dict[str, Any]) -> str:
    """A stable id for one assignment, so re-importing updates instead of duplicating."""
    handle = item.get("column_id") or item.get("content_id") or item.get("title") or "item"
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", str(handle))
    return f"bb-{safe}@{UID_DOMAIN}"


def _event(item: dict[str, Any], stamp: str) -> list[str] | None:
    """One VEVENT, or None for an assignment with no due date to place."""
    end = _utc(item.get("due_utc") or item.get("due_local"))
    if not end:
        return None
    due = datetime.strptime(end, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    start = (due - timedelta(minutes=BLOCK_MINUTES)).strftime("%Y%m%dT%H%M%SZ")

    course = item.get("course") or ""
    title = item.get("title") or "(untitled assignment)"
    points = item.get("points_possible")
    summary = f"{course}: {title}" if course else title

    description = "\n".join(filter(None, [
        item.get("course_name") or course or None,
        f"Due {due.astimezone().strftime('%a %b %d, %I:%M %p')}",
        f"{points:g} points" if points else None,
        "Already submitted" if item.get("submitted") else None,
    ]))

    lines = [
        "BEGIN:VEVENT",
        f"UID:{_uid(item)}",
        f"DTSTAMP:{stamp}",
        f"DTSTART:{start}",
        f"DTEND:{end}",
        f"SUMMARY:{_esc(summary)}",
        f"DESCRIPTION:{_esc(description)}",
        "STATUS:CONFIRMED",
        # A deadline should not make the student look busy to anyone else.
        "TRANSP:TRANSPARENT",
    ]
    if course:
        lines.append(f"CATEGORIES:{_esc(course)}")

    # The dashboard's own grid reads these back out of the feed: they are what
    # lets it colour by course and link a chip to its assignment.
    for prop, value in (
        ("X-BB-COURSE", course),
        ("X-BB-COURSE-NAME", item.get("course_name")),
        ("X-BB-COURSE-ID", item.get("course_id")),
        ("X-BB-COLUMN-ID", item.get("column_id")),
        ("X-BB-CONTENT-ID", item.get("content_id")),
        ("X-BB-TITLE", title),
    ):
        if value:
            lines.append(f"{prop}:{_esc(value)}")
    if points is not None:
        lines.append(f"X-BB-POINTS:{points:g}")
    lines.append(f"X-BB-SUBMITTED:{'TRUE' if item.get('submitted') else 'FALSE'}")

    lines += [
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        "TRIGGER:-P1D",
        f"DESCRIPTION:{_esc(f'Due tomorrow: {summary}')}",
        "END:VALARM",
        "END:VEVENT",
    ]
    return lines


def build(items: Iterable[dict[str, Any]], *, calname: str = "Coursework",
          description: str | None = None, now: datetime | None = None) -> str:
    """Render assignments as a complete VCALENDAR document."""
    stamp = _utc(now or datetime.now(timezone.utc)) or ""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_esc(calname)}",
        # Subscribed clients poll on their own schedule; ask for hourly.
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]
    if description:
        lines.append(f"X-WR-CALDESC:{_esc(description)}")

    count = 0
    for item in items:
        event = _event(item, stamp)
        if event:
            lines += event
            count += 1
    lines.append("END:VCALENDAR")

    folded: list[str] = []
    for line in lines:
        folded += _fold(line)
    # RFC 5545 wants CRLF line endings and a trailing break.
    return "\r\n".join(folded) + "\r\n"


def write(path: Path, text: str) -> Path:
    """Save the feed beside the cache so it can be imported without the server."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
    except OSError:
        pass
    return path
