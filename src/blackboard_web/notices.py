"""Which announcements have already been put in front of the reader.

The dashboard pops a new announcement up on load, and an announcement gets that
treatment exactly once. Which ones have had it cannot live in the browser: the
sidebar badge can, because "have you looked at this screen" really is a property
of the screen you read on, but "has this been announced at me" has to survive a
different browser, a cleared profile and a reinstall, or the popup comes back for
posts that were dealt with weeks ago. So it is kept beside the rest of the
knowledge store, in `announced.json`.

The first run is the awkward case: every announcement in the course is unseen by
this record, and forty of them stacked in a modal is not a notification, it is a
wall. So the first run writes the baseline instead of showing it — the popup
means "posted since you set this up", which is the only thing it can honestly
mean.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .cache import Cache

KEY = "announced"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load(cache: Cache) -> dict[str, str]:
    """Announcement id -> when it was shown."""
    stored = cache.get_data(KEY)
    if not isinstance(stored, dict):
        return {}
    shown = stored.get("shown")
    return shown if isinstance(shown, dict) else {}


def save(cache: Cache, shown: dict[str, str]) -> dict[str, str]:
    cache.write(KEY, {"shown": shown})
    return shown


def mark(cache: Cache, ids: list[str]) -> dict[str, str]:
    """Record that these have been shown. Already-recorded ones keep their time."""
    shown = load(cache)
    stamp = _now()
    for announcement_id in ids:
        if announcement_id and announcement_id not in shown:
            shown[announcement_id] = stamp
    return save(cache, shown)


def pending(cache: Cache, announcements: list[dict[str, Any]]) -> list[str]:
    """The ids worth popping up, newest first.

    `announcements` arrives sorted newest first and is capped, so anything that
    has aged out of it can never pop up — which is the right answer: a post the
    dashboard no longer lists is not news.
    """
    ids = [a["id"] for a in announcements if a.get("id")]
    if cache.read(KEY) is None:
        # First run: everything present becomes the baseline, silently.
        save(cache, {i: _now() for i in ids})
        return []
    shown = load(cache)
    return [i for i in ids if i not in shown]
