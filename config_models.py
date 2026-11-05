"""Deprecated import location.

The configuration dataclasses moved into the package:
``from spec2test.config import AppConfig``. This shim keeps older scripts
working and will be removed once they have been updated.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from spec2test.config import *  # noqa: F401,F403,E402
from spec2test.config import __all__ as _all  # noqa: E402

__all__ = list(_all) if isinstance(_all, (list, tuple)) else []
