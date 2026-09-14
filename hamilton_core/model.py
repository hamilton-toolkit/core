"""Read-only readers for the spec model, shared by the view commands
(`hamilton tree`, `hamilton show`). Nothing here writes, and none of it feeds
`hamilton check`.

The model is one tree (D-014): `spec/requirements.md`, whose interior nodes are
the architecture and carry an `Interface:`. `spec/actors.md` is a flat supporting
list. `hamilton check` owns the requirement extractor; this module reuses it,
adds the actor reader, and the derived views the data model calls for:
computed dotted paths, rolled-up coverage, reverse links.
"""

from __future__ import annotations

import os
import re

from hamilton_core.check import (REQ_REL, UsageError, extract, read_config,
                                 read_verified, scan, sha)

ACTORS_REL = "spec/actors.md"

TYPE_NAME = {"R": "requirement", "A": "actor"}


# --------------------------------------------------------------------------- #
# file readers                                                               #
# --------------------------------------------------------------------------- #

def _entity_lines(path: str):
    """[(lineno, stripped)] for non-blank lines outside fenced code, or None if
    the file is absent. The fence skip keeps the template's worked example out
    of the model."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read().splitlines()
    except FileNotFoundError:
        return None
    out, in_fence = [], False
    for n, line in enumerate(raw, 1):
        s = line.strip()
        if s.startswith("```") or s.startswith("~~~"):
            in_fence = not in_fence
            continue
        if not in_fence and s:
            out.append((n, s))
    return out


def parse_actors(root: str) -> dict:
    """{A-id: {name, description, line}}."""
    rows = _entity_lines(os.path.join(root, ACTORS_REL))
    actors: dict = {}
    if rows is None:
        return actors
    a = None
    for n, s in rows:
        head = re.match(r"(#{2,6})\s+(.+)$", s)
        if head:
            m = re.match(r"(A-\d{4})\b", head.group(2).strip())
            a = m.group(1) if (head.group(1) == "##" and m) else None
            if a:
                actors.setdefault(a, {"name": None, "description": None,
                                      "line": n})
            continue
        if a is None:
            continue
        m = re.match(r"([A-Za-z][\w -]*?):\s?(.*)$", s)
        if not m:
            continue
        key, val = m.group(1).strip().lower(), m.group(2).strip()
        if key == "name":
            actors[a]["name"] = val or None
        elif key == "description":
            actors[a]["description"] = val or None
    return actors


# --------------------------------------------------------------------------- #
# derived views                                                              #
# --------------------------------------------------------------------------- #

def dotted_paths(parent_of: dict) -> dict:
    """{id: "n.m.k"} from {id: parent_id_or_None}. Roots -- parent absent or
    unresolved -- are numbered by sorted id at the top level; children by
    sorted id under their parent. A parent link that would revisit an ancestor
    is dropped (the node becomes a root) so a malformed cyclic model still
    renders. Nothing stores this; it is recomputed every call."""
    children: dict = {}
    roots = []
    for cid in sorted(parent_of):
        par = parent_of[cid]
        if par in parent_of and par != cid:
            children.setdefault(par, []).append(cid)
        else:
            roots.append(cid)
    out: dict = {}

    def walk(node, prefix):
        out[node] = prefix
        for j, kid in enumerate(sorted(children.get(node, [])), 1):
            if kid not in out:                  # guard: a cycle would recurse
                walk(kid, f"{prefix}.{j}")

    n = 0
    # real roots first; then anything still unplaced -- a cycle's members --
    # is promoted to a root in id order so the model still renders.
    for node in roots + sorted(parent_of):
        if node not in out:
            n += 1
            walk(node, str(n))
    return out


def path_key(dotted: str):
    """Sort key that orders '2' before '10' and '1.2' before '1.10'."""
    if not dotted or not dotted[0].isdigit():
        return (9_999,)
    return tuple(int(x) for x in dotted.split("."))


def oneline(text: str | None, limit: int = 80) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def req_title(rid: str, reqs: dict) -> str | None:
    r = reqs.get(rid)
    if not r:
        return None
    return r["title"] or (oneline(r["statement"], 44) if r["statement"] else None)


def req_label(rid: str, reqs: dict) -> str:
    t = req_title(rid, reqs)
    return f'{rid} "{t}"' if t else f"{rid} (untitled)"


def actor_label(aid: str, actors: dict) -> str:
    name = (actors.get(aid) or {}).get("name")
    return f'{aid} "{name}"' if name else f"{aid} (unnamed)"


# priority order for the rolled-up requirement mark in `hamilton tree`
_STATUS_ORDER = ["unknown", "no ACs", "uncovered", "stale", "covered"]


class Model:
    """The spec parsed once, plus coverage. Constructed per command from the
    working directory; never mutated."""

    def __init__(self, root: str):
        self.root = root
        req_path = os.path.join(root, REQ_REL)
        if os.path.isfile(req_path):
            self.reqs, self.duplicates, self.malformed = extract(req_path)
        else:
            self.reqs, self.duplicates, self.malformed = {}, [], []
        self.actors = parse_actors(root)
        try:
            cfg = read_config(root)
            self.coverage_known = True
            test_paths = cfg.get("test_paths", ("", 0))[0]
        except UsageError:
            self.coverage_known = False
            test_paths = ""
        self.tags: dict = {}
        for q, a, f, ln in scan(root, test_paths):
            self.tags.setdefault((q, a), []).append((f, ln))
        self.covered = set(self.tags)
        self.verified = read_verified(root)

    # -- coverage ---------------------------------------------------------- #

    def ac_status(self, rid: str, acid: str, text: str) -> str:
        if not self.coverage_known:
            return "unknown"
        if (rid, acid) not in self.covered:
            return "uncovered"
        seen = self.verified.get(f"{rid}/{acid}")
        if seen is not None and seen != sha(text):
            return "stale"
        return "covered"

    def req_status(self, rid: str) -> str:
        r = self.reqs.get(rid)
        if r is None:
            return "unknown"
        if not r["acs"]:
            return "no ACs"
        seen = {self.ac_status(rid, a, ac["text"]) for a, ac in r["acs"].items()}
        for s in _STATUS_ORDER:
            if s in seen:
                return s
        return "covered"

    # -- reverse links --------------------------------------------------- #

    def child_requirements(self, rid: str):
        return sorted(k for k, r in self.reqs.items() if r["parent"] == rid)

    def requirements_for_actor(self, aid: str):
        return sorted(k for k, r in self.reqs.items() if r.get("actor") == aid)
