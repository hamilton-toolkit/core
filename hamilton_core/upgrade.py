"""`hamilton upgrade [path]` -- bring the framework-managed scaffold files up
to date.

Re-copies the framework-owned scaffold files -- AGENTS.md, CLAUDE.md and the
.claude/ tree (the guard hook settings and the `hamilton` skill) -- from the
installed templates. These belong to the framework, not the project, so
`upgrade` overwrites them with the current templates without asking and
recreates any that were deleted. It also deletes scaffold files a past `init`
wrote that the framework has since retired (`init.RETIRED`).

The project's own files are never touched: spec/, .hamilton/phase,
.hamilton/config, .hamilton/verified.

Every change is shown as a unified diff. No migration logic and no
version-compatibility checks: this copies files.

Exit: 0 upgraded or already current, 1 not a Hamilton project.
"""

from __future__ import annotations

import difflib
import os
import sys

from hamilton_core import __version__
from hamilton_core.init import MANAGED, RETIRED, SRC_FOR, TEMPLATES


def _read(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def _new_texts() -> dict:
    return {rel: _read(os.path.join(TEMPLATES, SRC_FOR[rel])) for rel in MANAGED}


def _changes(root: str, new_texts: dict):
    """[(rel, old_or_None, new)] for every managed file whose content on disk
    differs from the current template. A missing file (old is None) is a change
    too -- `upgrade` recreates it."""
    out = []
    for rel, new_text in new_texts.items():
        cur = _read(os.path.join(root, rel))
        if cur != new_text:
            out.append((rel, cur, new_text))
    return out


def _retired_present(root: str):
    return [rel for rel in RETIRED if os.path.isfile(os.path.join(root, rel))]


def _diff(rel: str, old: str | None, new: str) -> str:
    return "".join(difflib.unified_diff(
        (old or "").splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile="a/" + rel, tofile="b/" + rel,
    ))


def _write(root: str, rel: str, text: str) -> None:
    target = os.path.join(root, rel)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(text)


def _delete(root: str, rel: str) -> None:
    os.remove(os.path.join(root, rel))
    # prune now-empty parent dirs, but never the project root
    d = os.path.dirname(os.path.join(root, rel))
    while os.path.abspath(d) != os.path.abspath(root) and not os.listdir(d):
        os.rmdir(d)
        d = os.path.dirname(d)


def main(path: str | None = None) -> int:
    root = os.path.abspath(path or ".")
    if not os.path.isdir(os.path.join(root, ".hamilton")):
        print("hamilton upgrade: no .hamilton/ here -- not a Hamilton project. "
              "Run `hamilton init` first.", file=sys.stderr)
        return 1

    new_texts = _new_texts()
    missing = sorted(rel for rel, txt in new_texts.items() if txt is None)
    if missing:
        print(f"hamilton upgrade: this Hamilton install is missing template(s) "
              f"for {', '.join(missing)} -- the package is broken. Reinstall "
              f"hamilton and try again.", file=sys.stderr)
        return 1

    changes = _changes(root, new_texts)
    retired = _retired_present(root)
    if not changes and not retired:
        print(f"hamilton upgrade: already current; scaffold matches "
              f"hamilton {__version__}.")
        return 0

    for rel, old, new in changes:
        print(f"\n=== {rel} ===")
        sys.stdout.write(_diff(rel, old, new))
        _write(root, rel, new)
    for rel in retired:
        print(f"\n=== {rel} (retired -- deleted) ===")
        _delete(root, rel)

    n = len(changes) + len(retired)
    print(f"\nhamilton upgrade: updated {n} file(s) to hamilton {__version__}.")
    return 0
