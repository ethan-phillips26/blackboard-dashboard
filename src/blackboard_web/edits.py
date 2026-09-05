"""Local corrections layered over what Blackboard returns.

Blackboard access here is read-only, and a refresh overwrites the cached copy of
every assignment every fifteen minutes. So a correction a student makes — a due
date the instructor moved in class but never in Blackboard, a column name that
means nothing out of context, a weighting the syllabus states differently from
what the model read — cannot live in the fetched data. It is kept in its own
cache entry and re-applied on every read, which means a sync can never quietly
undo it.

Nothing here is sent back to Blackboard. These edits change what this dashboard
shows and what its calendar feed publishes, and nothing else.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .cache import Cache

KEY = "edits"

# What a student is allowed to change about an assignment. Everything else about
# it stays whatever Blackboard says it is.
ASSIGNMENT_FIELDS = ("title", "due_utc", "points_possible", "hidden", "note")


def load(cache: Cache) -> dict[str, Any]:
    stored = cache.get_data(KEY) or {}
    return {
        "assignments": stored.get("assignments") or {},
        "weights": stored.get("weights") or {},
    }


def save(cache: Cache, data: dict[str, Any]) -> dict[str, Any]:
    cache.write(KEY, data)
    return data


def item_key(item: dict[str, Any]) -> str | None:
    """How an assignment is addressed. Matches the key `_track_new` uses."""
    return item.get("column_id") or item.get("content_id")


def set_assignment(cache: Cache, key: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Record an edit. A field set to None is a field handed back to Blackboard."""
    data = load(cache)
    current = dict(data["assignments"].get(key) or {})
    for field in ASSIGNMENT_FIELDS:
        if field not in patch:
            continue
        value = patch[field]
        if value is None or value == "":
            current.pop(field, None)
        else:
            current[field] = value
    if current:
        data["assignments"][key] = current
    else:
        data["assignments"].pop(key, None)
    return save(cache, data)


def clear_assignment(cache: Cache, key: str) -> dict[str, Any]:
    data = load(cache)
    data["assignments"].pop(key, None)
    return save(cache, data)


def set_weights(cache: Cache, course_id: str, weights: dict[str, float]
                ) -> dict[str, Any]:
    """Override the weight on syllabus components, keyed by their label.

    Only the percentage is editable. The labels are what the gradebook's rows
    were sorted into, so renaming one here would orphan every row under it.
    """
    data = load(cache)
    cleaned = {str(k): float(v) for k, v in (weights or {}).items()}
    if cleaned:
        data["weights"][course_id] = cleaned
    else:
        data["weights"].pop(course_id, None)
    return save(cache, data)


def clear_weights(cache: Cache, course_id: str) -> dict[str, Any]:
    data = load(cache)
    data["weights"].pop(course_id, None)
    return save(cache, data)


def weights_for(cache: Cache, course_id: str) -> dict[str, float]:
    return load(cache)["weights"].get(course_id) or {}


def _reparse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def apply_assignments(items: list[dict[str, Any]], edits: dict[str, Any],
                      *, keep_hidden: bool = False) -> list[dict[str, Any]]:
    """Return the assignment list as the student has corrected it.

    A moved due date has to carry its derived fields with it — the local time the
    row prints, the countdown beside it and the order of the list all come from
    the due date, so recomputing them here is what makes an edit look real rather
    than half-applied.
    """
    overrides = edits.get("assignments") or {}
    if not overrides:
        return items

    now = datetime.now(timezone.utc)
    out: list[dict[str, Any]] = []
    for item in items:
        key = item_key(item)
        patch = overrides.get(key or "")
        if not patch:
            out.append(item)
            continue
        if patch.get("hidden") and not keep_hidden:
            continue

        edited = {**item, "edited": sorted(k for k in patch if k != "hidden")}
        if patch.get("hidden"):
            edited["hidden"] = True
        if patch.get("title"):
            edited["title"] = patch["title"]
        if patch.get("note"):
            edited["note"] = patch["note"]
        if "points_possible" in patch:
            edited["points_possible"] = patch["points_possible"]
        due = _reparse(patch.get("due_utc"))
        if due:
            edited["due_utc"] = due.isoformat()
            edited["due_local"] = due.astimezone().isoformat()
            edited["days_until"] = round((due - now).total_seconds() / 86400, 1)
        out.append(edited)

    # A moved deadline belongs where its new date puts it, not where its old one
    # left it in the list.
    out.sort(key=lambda a: a.get("due_utc") or "9999")
    return out
