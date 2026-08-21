from __future__ import annotations

import sys
from pathlib import Path


PORTABLE_MARKER = "portable.flag"


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def is_portable() -> bool:
    return (application_root() / PORTABLE_MARKER).is_file()


def portable_data_folder() -> Path:
    return application_root() / "data"
