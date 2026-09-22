"""The one change `hamilton build` makes to the spec: a criterion clarified by
the engineer.

A reviewer (or the planner) found a criterion the spec cannot settle -- its
wording leaves the outcome open, or no test by its method could satisfy it.
The engineer answers the question. A worker drafts an **amendment** to the
criterion's requirement from that answer, the way `hamilton design` drafts a
change: the criterion reworded, its method changed, new criteria added
beside it. The engineer sees it before and after, and may have it changed or
write it. Only then does Hamilton write it -- itself, not an agent. The phase
gate still refuses every agent write to `spec/`: build phase changes the
spec only with the engineer's approval of the exact lines.

What an amendment may do is bounded: it touches one requirement's criteria
-- the one being clarified, and new ones added after the last -- and uses
only methods the spec defines (or `manual`). A method that does not exist
yet is design work, and goes back to `hamilton design`.

A changed criterion has a new hash, so its tests come back as *AC changed*; a
new one is *uncovered*; one that became `manual` stops counting. The next
pass of the loop takes it from there.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from importlib import resources
from string import Template

from hamilton_core.check import MANUAL, REQ_REL, extract, extract_methods

NEW = "NEW"
_LINE_RE = re.compile(r"^-\s+(AC\d+|NEW):\s+(.+?)\s*$")
_MARKER_RE = re.compile(r"\[([^\]]+)\]\s*$")


@dataclass
class Amendment:
    """What the engineer approves: the criterion as it will read, and the
    criteria added after it -- numbered by Hamilton, not the drafter."""
    qual: str
    old: str                        # the criterion's line as it is
    new: str                        # ... and as it will be
    added: list = field(default_factory=list)       # full `- ACn: ...` lines
    draft: str = ""                 # what the drafter answered, for a revision


def _spec(root: str) -> tuple[dict, dict]:
    path = os.path.join(root, REQ_REL)
    reqs, _d, _m = extract(path)
    return reqs, extract_methods(path)


def _lines(root: str) -> list:
    with open(os.path.join(root, REQ_REL), encoding="utf-8") as fh:
        return fh.read().splitlines()


def prompt(root: str, qual: str, question: str, answer: str,
           draft: str = "", change: str = "") -> str:
    """The drafter's brief. With `draft` and `change` it revises its earlier
    draft the way the engineer asked."""
    reqs, defined = _spec(root)
    rid, acid = qual.split("/")
    req = reqs[rid]
    lines = _lines(root)
    criteria = "\n".join(lines[a["line"] - 1].strip() for a in req["acs"].values())
    methods = "\n".join(f"- `{m}` -- {d['description']}" for m, d in defined.items())
    earlier = ""
    if draft:
        earlier = (f"## Your earlier draft\n\n```\n{draft.strip()}\n```\n\n"
                   f"## What the engineer wants changed in it\n\n{change}\n\n")
    template = resources.files("hamilton_core").joinpath("prompts/clarify.md")
    return Template(template.read_text(encoding="utf-8")).substitute(
        rid=rid, title=req.get("title") or "", statement=req.get("statement") or "(none)",
        criteria=criteria, acid=acid, question=question, answer=answer,
        methods=methods, manual=MANUAL, earlier=earlier)


def parse(root: str, qual: str, draft: str) -> Amendment:
    """The draft as an amendment. Raises ValueError unless it is the one
    criterion's line, then any number of `- NEW:` lines, each ending in a
    method the spec defines (or `manual`)."""
    reqs, defined = _spec(root)
    rid, acid = qual.split("/")
    if "NEW METHOD NEEDED" in draft:
        need = draft.split("NEW METHOD NEEDED", 1)[1].strip(" :\n`")
        raise ValueError(f"that needs a verification method the spec does not "
                         f"define yet ({need or 'see the answer'}) -- define it "
                         f"in `hamilton design`")
    found = [m for m in (_LINE_RE.match(ln.strip())
                         for ln in draft.strip().strip("`").splitlines()) if m]
    if not found or found[0].group(1) != acid:
        raise ValueError(f"the draft does not start with the '- {acid}:' line")
    if any(m.group(1) != NEW for m in found[1:]):
        raise ValueError("the draft changes criteria other than the one asked about")
    for m in found:
        marker = _MARKER_RE.search(m.group(2))
        methods = [x.strip() for x in marker.group(1).split(",")] if marker else []
        unknown = [x for x in methods if x != MANUAL and x not in defined]
        if not methods or unknown:
            raise ValueError(f"the draft names a method the spec does not define "
                             f"({', '.join(unknown) or 'none'}) -- define it in "
                             f"`hamilton design`")
    n = max(int(a[2:]) for a in reqs[rid]["acs"])
    line = reqs[rid]["acs"][acid]["line"]
    old = _lines(root)[line - 1]
    indent = old[:len(old) - len(old.lstrip())]
    return Amendment(qual, old, f"{indent}- {acid}: {found[0].group(2)}",
                     [f"{indent}- AC{n + i}: {m.group(2)}"
                      for i, m in enumerate(found[1:], 1)],
                     draft)


def diff(a: Amendment, paint) -> list:
    """The criterion before and after, and what is added. The spec lines' own
    list bullets are left out, so `-` and `+` are the only marks."""
    def bare(line: str) -> str:
        return line.strip().removeprefix("- ")
    out = []
    if a.new.strip() != a.old.strip():
        out += [paint.red(f"  - {bare(a.old)}"), paint.green(f"  + {bare(a.new)}")]
    out += [paint.green(f"  + {bare(line)}") for line in a.added]
    return out


def write(root: str, a: Amendment) -> list:
    """Apply the amendment to the spec -- that criterion's line, and the new
    ones after the requirement's last criterion -- and nothing else. Returns
    the criteria it touched."""
    reqs, _defined = _spec(root)
    rid, acid = a.qual.split("/")
    acs = reqs[rid]["acs"]
    path = os.path.join(root, REQ_REL)
    with open(path, encoding="utf-8", newline="") as fh:
        parts = re.split(r"(\r\n|\r|\n)", fh.read())
    at = 2 * (acs[acid]["line"] - 1)            # parts alternate: line, ending
    if parts[at] != a.old:
        raise ValueError(f"{REQ_REL}:{acs[acid]['line']} changed while the run "
                         f"was waiting")
    parts[at] = a.new
    if a.added:
        last = 2 * (max(x["line"] for x in acs.values()) - 1)
        ending = parts[last + 1] if last + 1 < len(parts) and parts[last + 1] else "\n"
        parts[last] += "".join(ending + line for line in a.added)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("".join(parts))
    return [a.qual] + [f"{rid}/{line.strip()[2:].split(':')[0]}" for line in a.added]
