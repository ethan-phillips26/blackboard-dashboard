"""Session cookie persistence.

Blackboard's router reissues the BbRouter cookie on activity, each time with a
fresh `expires`. A browser stays logged in because it keeps writing that new
value back into its cookie jar. This module does the same thing for the MCP
server: whatever value the server last saw is written to disk and used in place
of the (static, quickly stale) seed value in .env.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import paths


def parse_bbrouter(value: str) -> dict[str, str]:
    """Split a BbRouter value's `key:value,key:value` payload into a dict."""
    out: dict[str, str] = {}
    payload = value.strip()
    if payload.lower().startswith("bbrouter="):
        payload = payload.split("=", 1)[1]
    for part in payload.split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def cookie_expiry(value: str) -> datetime | None:
    """The absolute expiry baked into a BbRouter value, if it has one."""
    raw = parse_bbrouter(value).get("expires")
    if not raw or not raw.isdigit():
        return None
    try:
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def seconds_remaining(value: str) -> float | None:
    exp = cookie_expiry(value)
    return None if exp is None else (exp - datetime.now(timezone.utc)).total_seconds()


def default_session_path() -> Path:
    """Sits next to .env by default; override with BB_SESSION_FILE."""
    return paths.state_file(".bb_session.json", "BB_SESSION_FILE")


class SessionStore:
    """Reads and writes the most recently seen session cookie."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_session_path()

    def load(self, host: str | None = None) -> str | None:
        """The stored cookie, or None if it belongs to a different host.

        A session cookie is only meaningful to the institution that issued it,
        and replaying one against another host would leak it, so a stored cookie
        whose host does not match is ignored rather than used.
        """
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return None
        cookie = data.get("cookie")
        if not (isinstance(cookie, str) and cookie):
            return None
        stored_host = data.get("host")
        if host and stored_host and stored_host != host:
            return None
        return cookie

    def save(self, cookie: str, host: str | None = None) -> None:
        exp = cookie_expiry(cookie)
        payload = {
            "cookie": cookie,
            "host": host,
            "updated": datetime.now(timezone.utc).isoformat(),
            "expires": exp.isoformat() if exp else None,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Write via a temp file so a crash mid-write cannot leave a truncated
            # cookie behind, and keep it readable only by this user.
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".bbsess")
            try:
                os.write(fd, json.dumps(payload, indent=2).encode())
            finally:
                os.close(fd)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except OSError:
            pass  # a read-only checkout should not break every tool call

    def info(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}


def best_cookie(env_cookie: str | None, store: SessionStore,
                host: str | None = None) -> tuple[str | None, str]:
    """Pick between the .env seed and the stored value, preferring the fresher.

    Returns (cookie, source). A cookie the user has just pasted into .env should
    win over a stale stored one, and vice versa, so compare their expiry fields
    rather than trusting either by position.
    """
    stored = store.load(host)
    if not stored:
        return env_cookie, "env"
    if not env_cookie:
        return stored, "session file"
    env_left = seconds_remaining(env_cookie)
    stored_left = seconds_remaining(stored)
    if env_left is None or stored_left is None:
        # Without expiry info on both, prefer the stored one: it is the value the
        # server most recently saw work.
        return stored, "session file"
    if env_left > stored_left:
        return env_cookie, "env (newer than stored session)"
    return stored, "session file (auto-renewed)"
