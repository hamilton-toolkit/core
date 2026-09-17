"""`hamilton tree` -- the requirement tree.

One row per requirement: the computed dotted path, the id in
parentheses, a one-line statement, an `>` marker on an interior node that
carries an `Interface:`, and a rolled-up coverage mark. Read-only; `--json`
emits the rows as data. Exit 0, or 2 outside a project.
"""

from __future__ import annotations

import json
import os
import sys

from hamilton_core import model as M


def rows(root: str) -> list[dict]:
    """The requirement tree as rows in path order -- what `hamilton tree`
    prints, and what anything else offering the tree should read."""
    m = M.Model(root)
    paths = M.dotted_paths({k: r["parent"] for k, r in m.reqs.items()})
    order = sorted(m.reqs, key=lambda k: M.path_key(paths.get(k, "")))
    has_child = {r["parent"] for r in m.reqs.values() if r["parent"] in m.reqs}
    out = []
    for rid in order:
        r = m.reqs[rid]
        out.append({
            "path": paths.get(rid, "?"),
            "id": rid,
            "title": M.req_title(rid, m.reqs),
            "label": M.req_label(rid, m.reqs),
            "statement": r["statement"],
            "parent": r["parent"],
            "actor": r.get("actor"),
            "interface": r["interface"],
            "boundary": rid in has_child,
            "status": m.req_status(rid),
            "criteria": {a: m.ac_status(rid, a, ac["text"])
                         for a, ac in sorted(r["acs"].items())},
            "_text": M.oneline(r["statement"] or r["title"] or "(no statement)"),
        })
    return out


def _render(rows: list[dict]) -> str:
    if not rows:
        return "hamilton tree: spec/requirements.md declares no requirements"
    wp = max(len(r["path"]) for r in rows)
    wi = max(len(r["id"]) for r in rows)
    out = []
    for r in rows:
        mark = "i" if (r["boundary"] and r["interface"]) else \
               "!" if r["boundary"] else " "
        out.append(f"{r['path']:<{wp}}  {mark} ({r['id']:<{wi}})  "
                   f"{r['_text']}   [{r['status']}]")
    out.append("")
    out.append("  i = interior node with an Interface:   ! = interior, no Interface: yet")
    return "\n".join(out)


def main(as_json: bool = False) -> int:
    root = os.getcwd()
    if not os.path.isdir(os.path.join(root, "spec")):
        print("hamilton tree: spec/ not found (run from the project root)",
              file=sys.stderr)
        return 2
    found = rows(root)
    if as_json:
        print(json.dumps({"rows": [{k: v for k, v in r.items()
                                    if not k.startswith("_")} for r in found]}))
    else:
        print(_render(found))
    return 0
