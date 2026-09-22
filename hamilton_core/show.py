"""`hamilton show [ID] [--json]` -- the requirement tree, or one entity in full.

Without an id it is the requirement tree: one row per requirement with the
computed dotted path, the id, a one-line statement and a rolled-up coverage
mark. On a terminal the engineer walks it -- fold, unfold, open one
requirement's full view; anywhere else (an agent, a pipe) it is printed.

With an id it works for the two id types the model has (D-014): `R-` requirement, `A-`
actor. Each view shows the entity's own fields *and what refers to it* -- a
requirement's children, an actor's requirements -- so a reviewer never has to
grep the model by hand. Ids are always rendered with their title/name, never
bare (SKILL.md rendering rule 1).

Read-only; `--json` emits either as data. Exit 0 on success, 2 on a bad id or
when run outside a project.
"""

from __future__ import annotations

import json
import os
import re
import sys

from hamilton_core import model as M
from hamilton_core.verify import refs_in

_ID = re.compile(r"[RA]-\d{4}")


def rows(root: str) -> list[dict]:
    """The requirement tree as rows in path order -- what `hamilton show`
    lists, and what anything else offering the tree should read."""
    return _rows(M.Model(root))


def _rows(m: M.Model) -> list[dict]:
    paths = M.dotted_paths({k: r["parent"] for k, r in m.reqs.items()})
    order = sorted(m.reqs, key=lambda k: M.path_key(paths.get(k, "")))
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
            "status": m.req_status(rid),
            "criteria": {a: m.ac_status(rid, a) for a in sorted(r["acs"])},
            "_text": M.oneline(r["statement"] or r["title"] or "(no statement)"),
        })
    return out


def _render_tree(rows: list[dict]) -> str:
    wp = max(len(r["path"]) for r in rows)
    wi = max(len(r["id"]) for r in rows)
    out = []
    for r in rows:
        out.append(f"{r['path']:<{wp}}  ({r['id']:<{wi}})  "
                   f"{r['_text']}   [{r['status']}]")
    return "\n".join(out)


def _browse_tree(m: M.Model, rows: list[dict]) -> None:
    from hamilton_core.session import widgets
    items = [(r["path"].count("."),
              f"{r['path']}  {r['label']}  \x1b[2m[{r['status']}]\x1b[0m",
              _view_requirement(m, r["id"])[1])
             for r in rows]
    widgets.tree(items).run()


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _req_path(rid: str, reqs: dict) -> list:
    chain, seen, node = [], set(), rid
    while node and node in reqs and node not in seen:
        seen.add(node)
        chain.append(node)
        node = reqs[node]["parent"]
    if node and node not in reqs:
        chain.append(node)                      # unresolved Parent: kept visible
    chain.reverse()
    return [M.req_title(n, reqs) or n for n in chain]


# --------------------------------------------------------------------------- #
# per-type views: each returns (data_dict, [text lines])                      #
# --------------------------------------------------------------------------- #

def _view_requirement(m: M.Model, rid: str):
    r = m.reqs[rid]
    path = _req_path(rid, m.reqs)
    crit = []
    for acid, ac in sorted(r["acs"].items()):
        st = m.ac_status(rid, acid)
        # `review` is None for a tag that does not count (wrong method)
        tags = [{"file": f, "line": ln, "review": m.reviews.get((f, ln))}
                for f, ln in m.tags.get((rid, acid), [])]
        crit.append({"id": acid, "text": ac["text"], "methods": ac["methods"],
                     "status": st, "tags": tags})
    children = [{"id": c, "title": M.req_title(c, m.reqs)}
                for c in m.child_requirements(rid)]
    refs = refs_in(" ".join([r["statement"] or ""]
                            + [ac["text"] for ac in r["acs"].values()]))
    data = {
        "id": rid, "type": "requirement", "title": r["title"],
        "path": path, "statement": r["statement"],
        "actor": r.get("actor"), "references": refs,
        "criteria": crit, "children": children,
    }

    lines = [M.req_label(rid, m.reqs), f"  path:       {' › '.join(path)}"]
    if r["parent"] is None:
        a = r.get("actor")
        if a and re.fullmatch(r"A-\d{4}", a):
            lines.append(f"  actor:      {M.actor_label(a, m.actors)}")
        elif a:
            lines.append(f'  actor:      {a} "(unresolved)"')
        else:
            lines.append("  actor:      (none — a root requirement must name one)")
    lines.append(f"  statement:  {r['statement'] or '(none — malformed)'}")
    if refs:
        lines.append("  references: " + ", ".join(
            ref if os.path.isfile(os.path.join(m.root, ref)) else f"{ref} (missing)"
            for ref in refs))
    lines.append("  criteria:")
    if not crit:
        lines.append("    (none — malformed)")
    for c in crit:
        if c["status"] == "unknown":
            tail = "[coverage unknown — no .hamilton/config]"
        else:
            where = ("; ".join(f"{t['file']}:{t['line']} "
                               f"({t['review'] or 'wrong method, does not count'})"
                               for t in c["tags"])
                     or "no @covers tag in any method's paths")
            tail = f"[{c['status']}]  {where}"
        lines.append(f"    {c['id']}  {c['text']}")
        lines.append(f"         {tail}")
    lines.append("  children:")
    if not children:
        lines.append("    (none)")
    for c in children:
        lines.append("    " + M.req_label(c["id"], m.reqs))
    return data, lines


def _view_actor(m: M.Model, aid: str):
    a = m.actors[aid]
    reqs = m.requirements_for_actor(aid)
    data = {"id": aid, "type": "actor", "name": a["name"],
            "description": a["description"], "requirements": reqs}

    lines = [M.actor_label(aid, m.actors),
             f"  description: {a['description'] or '(none)'}",
             "  named by requirements:"]
    if not reqs:
        lines.append("    (none)")
    for rid in reqs:
        lines.append("    " + M.req_label(rid, m.reqs))
    return data, lines


_VIEWS = {
    "R": (_view_requirement, "reqs"),
    "A": (_view_actor, "actors"),
}


def main(ident: str | None = None, as_json: bool = False) -> int:
    if ident is not None and not _ID.fullmatch(ident):
        print("hamilton show: give an entity id — R-nnnn or A-nnnn, "
              "e.g. `hamilton show R-0001`", file=sys.stderr)
        return 2
    root = os.getcwd()
    if not os.path.isdir(os.path.join(root, "spec")):
        print("hamilton show: spec/ not found (run from the project root)",
              file=sys.stderr)
        return 2

    m = M.Model(root)
    if ident is None:
        return _show_tree(m, as_json)
    kind = ident[0]
    view, attr = _VIEWS[kind]
    if ident not in getattr(m, attr):
        noun = M.TYPE_NAME[kind]
        print(f"hamilton show: {ident} is not declared as a {noun} in spec/",
              file=sys.stderr)
        return 2
    data, lines = view(m, ident)

    if as_json:
        print(json.dumps(data))
    else:
        print("\n".join(lines))
    return 0


def _show_tree(m: M.Model, as_json: bool) -> int:
    found = _rows(m)
    if as_json:
        print(json.dumps({"rows": [{k: v for k, v in r.items()
                                    if not k.startswith("_")} for r in found]}))
    elif not found:
        print("hamilton show: spec/requirements.md declares no requirements")
    elif _interactive():
        _browse_tree(m, found)
    else:
        print(_render_tree(found))
    return 0
