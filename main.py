#!/usr/bin/env python3
"""iPXE PVE Controller — application entry point.

Two ways to use this file:

    python main.py serve              # start the web server
    python main.py app:user:make      # create a user (interactive)
    python main.py                    # list available commands

Use the project virtualenv (`.venv`) so the dependencies are available.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the project root is importable regardless of the caller's CWD.
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    from core.cli.kernel import main as cli_main

    return cli_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
