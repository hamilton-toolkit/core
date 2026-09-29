"""The presentation: `spec/design-guide.md`, and what the build does with it.

Behaviour is specified and verified; presentation -- how the software looks
and feels -- is only described, and validated by the engineer. So nothing in
`hamilton verify` reads the guide, and a change to it makes no finding. The
build keeps its own record of the guide it last realised,
`.hamilton/presented`, and when the guide differs from it, a task realises
what changed (`present_prompt`).

The other half keeps presentation from moving when nothing asked it to: the
coding step's changes are judged for presentation changes no criterion calls
for (`drift_prompt`), and those go back to the coder once to be restored.

Everything a prompt needs about the guide is worded here, once.
"""

from __future__ import annotations

import difflib
import json
import os

from hamilton_core import verify as _verify
from hamilton_core.init import TEMPLATES

GUIDE_REL = "spec/design-guide.md"
RECORD_REL = os.path.join(".hamilton", "presented")

NO_GUIDE = ("There is no design guide (`spec/design-guide.md` is missing or "
            "still the template): keep to the presentation the project has.")

# A coding step's diff longer than this is not judged: a reviewer handed that
# much finds everything or nothing.
DRIFT_LINES = 3000


def guide(root: str) -> str | None:
    """The design guide's text, or None when there is none worth reading --
    missing, or still the template `hamilton init` wrote."""
    text = _verify.ref_text(_verify.spec_file(root, GUIDE_REL))
    if text is None:
        return None
    with open(os.path.join(TEMPLATES, GUIDE_REL), encoding="utf-8") as fh:
        stub = fh.read()
    return None if text.strip() == stub.strip() else text


def references(root: str, text: str) -> dict:
    """{path: digest} of the files the guide names that exist -- a mockup,
    a screenshot. The guide itself and the model are not among them."""
    out = {}
    for rel in _verify.refs_in(text):
        data = _verify.spec_file(root, rel)
        if rel != GUIDE_REL and data is not None:
            out[rel] = _verify.ref_digest(data)
    return out


def brief(root: str) -> str:
    """What a build task is told about the presentation."""
    if guide(root) is None:
        return NO_GUIDE
    return (f"`{GUIDE_REL}` describes how the software should look and feel, "
            f"with the files under `spec/` it names. It is intent, not a "
            f"criterion: no test asserts it. Read it before you touch the "
            f"presentation, and realise it as well as you can wherever you "
            f"do.")


# --- the record of what was realised -------------------------------------------

def recorded(root: str) -> dict:
    try:
        with open(os.path.join(root, RECORD_REL), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def current(root: str) -> dict:
    """The guide as it stands, in the record's shape; {} without one."""
    text = guide(root)
    return {"guide": text, "refs": references(root, text)} if text else {}


def changed(root: str) -> bool:
    """Whether the guide differs from the one last realised. No guide is
    never a change: there is nothing to realise."""
    now = current(root)
    return bool(now) and now != recorded(root)


def record(root: str) -> None:
    """The guide as it stands has been realised."""
    path = os.path.join(root, RECORD_REL)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(current(root), fh, indent=2, sort_keys=True)
        fh.write("\n")


def present_prompt(root: str, template) -> str:
    """The brief of the task that realises what changed in the guide."""
    before, now = recorded(root), current(root)
    if before.get("guide"):
        diff = "".join(difflib.unified_diff(
            before["guide"].splitlines(keepends=True),
            now["guide"].splitlines(keepends=True),
            "the guide last realised", "the guide now"))
        what = ("What changed in it since the presentation was last brought "
                f"in line:\n\n```diff\n{diff}```")
    else:
        what = ("The presentation has not been brought in line with it yet: "
                "the whole guide is new to it.")
    old, new = before.get("refs") or {}, now["refs"]
    refs = [f"- `{r}` ({'new' if r not in old else 'changed'})"
            for r in sorted(new) if old.get(r) != new[r]]
    refs += [f"- `{r}` (no longer named)" for r in sorted(set(old) - set(new))]
    return template.substitute(
        guide=GUIDE_REL, text=now["guide"].strip(), changes=what,
        refs="\n".join(refs) or "(none changed)")


# --- the drift review -------------------------------------------------------------

def drift_prompt(template, diff: str, criteria: str) -> str | None:
    """The reviewer's brief for a coding step's diff, or None when there is
    nothing to judge, or too much."""
    if not diff.strip() or diff.count("\n") > DRIFT_LINES:
        return None
    return template.substitute(diff=diff.rstrip(), criteria=criteria or "(none)")


def files(answer: str, key: str) -> dict:
    """{file: why} under `key` in an answer's JSON object -- what the
    reviewer `flagged`, what the restoring coder `kept`. An answer that does
    not parse names nothing: the review is advisory, and a garbled one is no
    reason to undo work."""
    start, end = answer.find("{"), answer.rfind("}")
    try:
        data = json.loads(answer[start:end + 1]) if 0 <= start < end else {}
    except ValueError:
        return {}
    named = data.get(key) if isinstance(data, dict) else None
    return ({str(k): str(v) for k, v in named.items()}
            if isinstance(named, dict) else {})
