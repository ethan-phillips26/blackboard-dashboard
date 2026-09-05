"""Entry point for the frozen sidecar the desktop shell spawns.

PyInstaller freezes a script, not a console-script entry point, so this is the
one-line script that stands in for `blackboard-web`.
"""

from __future__ import annotations

import multiprocessing

from blackboard_web.app import main

if __name__ == "__main__":
    # Windows re-executes the bundle for every child process; without this a
    # frozen app that ever spawns one forks bombs itself instead.
    multiprocessing.freeze_support()
    main()
