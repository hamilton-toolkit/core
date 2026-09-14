"""`hamilton show <ID> [--json]` -- print one entity in full.

Works for the two id types the model has (D-014): `R-` requirement, `A-`
actor. Each view shows the entity's own fields *and what refers to it* -- a
requirement's children, an actor's requirements -- so a reviewer never has to
grep the model by hand. Ids are always rendered with their title/name, never
bare (SKILL.md rendering rule 1).

Read-only. Exit 0 on success, 2 on a bad id or when run outside a project.
"""

from __future__ import annotations

import json
import os
import re
import sys

from hamilton_core import model as M

_ID = re.compile(r"[RA]-\d{4}")


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
        st = m.ac_status(rid, acid, ac["text"])
        tags = [{"file": f, "line": ln} for f, ln in m.tags.get((rid, acid), [])]
        crit.append({"id": acid, "text": ac["text"], "status": st, "tags": tags})
    children = [{"id": c, "title": M.req_title(c, m.reqs)}
                for c in m.child_requirements(rid)]
    boundary = bool(children)
    data = {
        "id": rid, "type": "requirement", "title": r["title"],
        "path": path, "statement": r["statement"],
        "actor": r.get("actor"), "interface": r["interface"],
        "boundary": boundary,
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
    if boundary:
        lines.append(f"  interface:  {r['interface'] or '(none stated yet)'}")
    lines.append("  criteria:")
    if not crit:
        lines.append("    (none — malformed)" if not boundary else "    (none)")
    for c in crit:
        if c["status"] == "unknown":
            tail = "[coverage unknown — no .hamilton/config]"
        else:
            where = ("; ".join(f"{t['file']}:{t['line']}" for t in c["tags"])
                     or "no @covers tag in any test path")
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
    if not ident or not _ID.fullmatch(ident):
        print("hamilton show: give an entity id — R-nnnn or A-nnnn, "
              "e.g. `hamilton show R-0001`", file=sys.stderr)
        return 2
    root = os.getcwd()
    if not os.path.isdir(os.path.join(root, "spec")):
        print("hamilton show: spec/ not found (run from the project root)",
              file=sys.stderr)
        return 2

    m = M.Model(root)
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
