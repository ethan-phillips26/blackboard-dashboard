#!/usr/bin/env python3
"""Freeze the dashboard server into the binary the desktop shell spawns.

Onedir, not onefile: a onefile bundle unpacks its whole ~200MB payload into a
temporary directory on every launch, which is both slow and the shape antivirus
heuristics flag hardest. Onedir starts immediately and looks like an ordinary
program directory.

The result lands in `src-tauri/sidecar/`, which `tauri.conf.json` ships as a
bundled resource.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "src" / "blackboard_web" / "frontend"
OUT = ROOT / "src-tauri" / "sidecar"
WORK = ROOT / "build" / "pyinstaller"

NAME = "blackboard-server"

# uvicorn resolves these by string at runtime, so nothing in the import graph
# points at them and PyInstaller cannot see them.
HIDDEN = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]


def declared_versions() -> dict[str, str]:
    """The version as each file that carries one states it.

    Three files now claim a version, and they answer different questions:
    pyproject backs `__version__` and so what `/api/health` reports, while
    tauri.conf.json is what the updater compares against the release feed. Let
    them drift and an installed copy reports one version while asking to be
    updated from another.
    """
    import json
    import re

    pyproject = (ROOT / "pyproject.toml").read_text()
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    found = {"pyproject.toml": match.group(1) if match else "?"}

    for name, path in (("tauri.conf.json", ROOT / "src-tauri" / "tauri.conf.json"),
                       ("package.json", ROOT / "package.json")):
        if path.exists():
            found[name] = json.loads(path.read_text()).get("version", "?")
    return found


def run(cmd: list[str], **kw) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def main() -> int:
    versions = declared_versions()
    if len(set(versions.values())) != 1:
        print("error: the declared versions disagree —", file=sys.stderr)
        for name, value in versions.items():
            print(f"  {name}: {value}", file=sys.stderr)
        print("Bump them together; the updater and /api/health read different "
              "ones.", file=sys.stderr)
        return 1

    # Always, not just when dist/ is missing. A stale dist is indistinguishable
    # from a fresh one at this point, and freezing it produces an app that runs
    # yesterday's UI with no sign that anything is wrong. The build takes a
    # second; the confusion it prevents does not.
    print("building the frontend", flush=True)
    run(["npm", "run", "build"], cwd=FRONTEND)

    if OUT.exists():
        shutil.rmtree(OUT)

    run([
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--name", NAME,
        # Console subsystem on purpose: the shell reads the announced port off
        # stdout. On Windows it spawns us with CREATE_NO_WINDOW, so no console
        # ever appears — a windowed build would leave sys.stdout as None.
        "--console",
        "--distpath", str(OUT),
        "--workpath", str(WORK),
        "--specpath", str(WORK),
        # The built frontend lives inside the package as data, not as modules.
        "--collect-data", "blackboard_web",
        # importlib.metadata backs __version__, and needs the dist-info present.
        "--copy-metadata", "blackboard-mcp",
        *[a for h in HIDDEN for a in ("--hidden-import", h)],
        str(ROOT / "scripts" / "sidecar_entry.py"),
    ])

    exe = OUT / NAME / (NAME + (".exe" if sys.platform == "win32" else ""))
    if not exe.exists():
        print(f"error: expected {exe} to exist", file=sys.stderr)
        return 1
    size = sum(f.stat().st_size for f in (OUT / NAME).rglob("*") if f.is_file())
    print(f"\nsidecar built: {exe}  ({size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
