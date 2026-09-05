"""Where this app keeps its state.

Every stateful path used to resolve against either the source tree
(`__file__.parents[2]`) or the process's working directory. Both stop being true
the moment the app is not run by hand from a checkout: a frozen bundle unpacks
into a temp directory that is deleted on exit, and an Electron shell starts the
server with whatever working directory it happens to inherit. Either way the
cache, the session cookie and the Google token get written somewhere that does
not survive, and the symptom is an app that silently forgets it was logged in.

So state has one home, decided here. A checkout that is already configured keeps
using its own directory — an `.env` sitting next to the source means somebody put
it there deliberately — so this change does not strand credentials and cookies
that are already on disk.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "blackboard"

# The repository root when running from source: src/blackboard_mcp/paths.py
SOURCE_ROOT = Path(__file__).resolve().parents[2]


def _user_data_dir() -> Path:
    """The conventional per-user data directory for this platform."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base).expanduser() / APP_NAME


def state_dir() -> Path:
    """The one directory holding .env, the session cookie and the caches.

    `BB_STATE_DIR` wins. Otherwise a source checkout that already has an `.env`
    keeps using itself, and everything else — a fresh clone, a packaged build —
    gets the per-user directory.
    """
    override = os.environ.get("BB_STATE_DIR")
    if override:
        root = Path(override).expanduser()
    elif (SOURCE_ROOT / ".env").exists():
        root = SOURCE_ROOT
    else:
        root = _user_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def state_file(name: str, override_env: str | None = None) -> Path:
    """One file inside the state directory, with its own env override honoured."""
    if override_env:
        override = os.environ.get(override_env)
        if override:
            return Path(override).expanduser()
    return state_dir() / name


def download_dir() -> Path:
    """Where handouts are saved.

    Pinned to the state directory rather than left to the working directory, so
    the path we save to and the path we serve back agree however the server was
    started.
    """
    override = os.environ.get("BB_DOWNLOAD_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return (state_dir() / "blackboard-files").resolve()
