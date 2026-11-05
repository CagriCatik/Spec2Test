#!/usr/bin/env python3
"""Launcher for the graphical interface.

The interface itself lives in ``src/spec2test/ui/``. After ``pip install -e .``
the console script ``spec2test-gui`` does the same thing.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from spec2test.ui.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
