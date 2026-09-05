"""A small JSON knowledge store on disk.

The dashboard should feel instant and should not re-ask Blackboard for things it
already knows. Every fetched resource lands here with a timestamp; reads are
served from disk until the entry ages past its TTL. Derived knowledge that is
expensive to produce (syllabus weightings, LLM summaries) is stored without a TTL
and only recomputed when its inputs actually change.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from blackboard_mcp import paths

# How long each kind of fetched data stays fresh, in seconds.
TTL = {
    "courses": 6 * 3600,       # enrolment changes at most once a term
    "categories": 24 * 3600,   # gradebook structure is essentially static
    "assignments": 900,
    "grades": 900,
    "announcements": 900,
    "content": 6 * 3600,
}


def cache_root() -> Path:
    override = os.environ.get("BB_CACHE_DIR")
    root = Path(override).expanduser() if override else \
        paths.state_dir() / ".blackboard-cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _now() -> datetime:
    return datetime.now(timezone.utc)


def digest(payload: Any) -> str:
    """A stable hash of any JSON-able value, used to detect real changes."""
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


class Cache:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or cache_root()

    def _path(self, key: str) -> Path:
        safe = key.replace("/", "_").replace("..", "_")
        return self.root / f"{safe}.json"

    def read(self, key: str) -> dict[str, Any] | None:
        try:
            return json.loads(self._path(key).read_text())
        except (OSError, ValueError):
            return None

    def write(self, key: str, data: Any, **extra: Any) -> dict[str, Any]:
        entry = {
            "fetched_at": _now().isoformat(),
            "digest": digest(data),
            "data": data,
            **extra,
        }
        path = self._path(key)
        try:
            fd, tmp = tempfile.mkstemp(dir=str(self.root), prefix=".tmp")
            try:
                os.write(fd, json.dumps(entry, indent=2, default=str).encode())
            finally:
                os.close(fd)
            os.replace(tmp, path)
        except OSError:
            pass
        return entry

    def age(self, key: str) -> float | None:
        """Seconds since this key was written, or None if it was never written."""
        entry = self.read(key)
        if not entry or "fetched_at" not in entry:
            return None
        try:
            fetched = datetime.fromisoformat(entry["fetched_at"])
        except ValueError:
            return None
        return (_now() - fetched).total_seconds()

    def fresh(self, key: str, kind: str | None = None, ttl: float | None = None) -> bool:
        if ttl is None:
            ttl = TTL.get(kind or key.split("/")[0], 900)
        age = self.age(key)
        return age is not None and age < ttl

    def get_data(self, key: str, default: Any = None) -> Any:
        entry = self.read(key)
        return entry["data"] if entry and "data" in entry else default

    def drop(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except OSError:
            pass

    def keys(self) -> list[str]:
        return sorted(p.stem for p in self.root.glob("*.json"))
