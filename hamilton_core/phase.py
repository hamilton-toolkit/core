"""`.hamilton/phase` -- the one file that says which phase a project is in.

Everything that reads or writes it goes through here.
"""

from __future__ import annotations

import os

PHASE_REL = os.path.join(".hamilton", "phase")


def read(root: str) -> str | None:
    """The phase, or None if the file is missing (not a Hamilton project)."""
    try:
        with open(os.path.join(root, PHASE_REL), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def write(root: str, phase: str) -> None:
    with open(os.path.join(root, PHASE_REL), "w", encoding="utf-8") as fh:
        fh.write(phase)
