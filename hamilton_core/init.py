"""`hamilton init [path]` -- scaffold a project.

Copies the tree under ``templates/`` into the target directory (dot-prefixing
the names that cannot be package directory entries). Refuses if ``.hamilton/``
already exists -- that directory is the initialised-project sentinel. Exit: 0
scaffolded, 1 refused.

What it writes is the project's from then on, to edit as it sees fit. The
workflows are not among it: the `hamilton` skill ships in the package and each
session loads it from there, so a newer Hamilton needs nothing re-copied.
"""

from __future__ import annotations

import os
import sys

TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

# template-relative path -> scaffold-relative path
LAYOUT = {
    "spec/vision.md": "spec/vision.md",
    "spec/actors.md": "spec/actors.md",
    "spec/requirements.md": "spec/requirements.md",
    "spec/design-guide.md": "spec/design-guide.md",
    "hamilton/phase": ".hamilton/phase",
    "hamilton/config": ".hamilton/config",
    # Agent instructions: AGENTS.md holds the rules, CLAUDE.md points at it.
    "AGENTS.md": "AGENTS.md",
    "CLAUDE.md": "CLAUDE.md",
    "claude/settings.json": ".claude/settings.json",
}


def _write(root: str, src: str) -> None:
    with open(os.path.join(TEMPLATES, src), "r", encoding="utf-8") as fh:
        body = fh.read()
    target = os.path.join(root, LAYOUT[src])
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(body)


def ensure(root: str, dst: str) -> bool:
    """Scaffold the one file `dst` if the project does not have it -- a file
    added to the scaffold after the project was initialised. Returns whether
    it was written."""
    if os.path.exists(os.path.join(root, dst)):
        return False
    _write(root, next(src for src, d in LAYOUT.items() if d == dst))
    return True


def main(path: str | None = None) -> int:
    root = os.path.abspath(path or ".")
    if os.path.exists(os.path.join(root, ".hamilton")):
        print(f"hamilton init: {os.path.join(root, '.hamilton')} already exists; "
              f"this project is already initialised. Nothing written. Remove "
              f".hamilton/ by hand to re-scaffold.", file=sys.stderr)
        return 1

    for src in LAYOUT:
        _write(root, src)

    print(f"hamilton init: scaffolded {root}\n\n"
          f"Run `hamilton design` to write the specification with an AI agent's "
          f"help.\nPrefer to write it by hand? Start from the files in spec/ -- "
          f"each explains what it should contain.\nAdopting an existing "
          f"codebase? Run `hamilton reverse` to derive the first spec from the "
          f"code.")
    return 0
