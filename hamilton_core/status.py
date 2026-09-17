"""`hamilton status` -- a read-only snapshot of the project, and the welcome
banner `hamilton design` / `hamilton build` print before they launch the agent.

`render(root, phase)` is pure: it parses the spec and scans for `@covers` tags
(the same readers `hamilton tree` uses) but never runs the test suite and never
writes. It reports the phase, the requirement / acceptance-criterion counts,
coverage, whether the gate has ever passed, and the last three `spec/` commits.
For a `build` banner it also shows `git diff --stat spec/` -- the uncommitted
spec change that a build session exists to propagate.

Exit 0, or 2 when run outside a project (no `spec/`).
"""

from __future__ import annotations

import os
import subprocess
import sys

from hamilton_core import model as M
from hamilton_core import phase as _phase_file
from hamilton_core.check import REQ_REL, extract

VERIFIED_REL = ".hamilton/verified"


def _phase(root: str, phase: str | None) -> str:
    return phase or _phase_file.read(root) or "unknown"


def _counts(root: str):
    """(n_reqs, n_acs) from spec/requirements.md, or (0, 0) if it is absent."""
    path = os.path.join(root, REQ_REL)
    if not os.path.isfile(path):
        return 0, 0
    reqs, _dupes, _malformed = extract(path)
    return len(reqs), sum(len(r["acs"]) for r in reqs.values())


def _coverage(root: str):
    """(covered, uncovered, stale) AC counts, or None when coverage is unknown
    (no .hamilton/config, so no test_paths to scan)."""
    m = M.Model(root)
    if not m.coverage_known:
        return None
    covered = uncovered = stale = 0
    for rid, r in m.reqs.items():
        for acid, ac in r["acs"].items():
            st = m.ac_status(rid, acid, ac["text"])
            if st == "covered":
                covered += 1
            elif st == "stale":
                stale += 1
            else:
                uncovered += 1
    return covered, uncovered, stale


def _git(root: str, *args) -> str | None:
    """`git -C root <args>` stdout, or None if git is missing / this is not a
    repo / the command failed."""
    try:
        p = subprocess.run(["git", "-C", root, *args],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    return p.stdout


def _recent_spec_changes(root: str) -> list[str]:
    out = _git(root, "log", "-n", "3", "--format=%h %ad %s", "--date=short",
               "--", "spec")
    if out is None:
        return ["(no git history for spec/)"]
    lines = [ln for ln in out.splitlines() if ln.strip()]
    return lines or ["(no commits touch spec/ yet)"]


def _uncommitted_spec(root: str) -> list[str]:
    out = _git(root, "diff", "--stat", "--", "spec")
    if not out:
        return []
    return [ln for ln in out.splitlines() if ln.strip()]


def render(root: str, phase: str | None = None) -> str:
    ph = _phase(root, phase)
    n_reqs, n_acs = _counts(root)
    cov = _coverage(root)
    verified = os.path.join(root, VERIFIED_REL)
    gate = ("gate last passed"
            if os.path.isfile(verified) and os.path.getsize(verified) > 0
            else "gate never passed")

    rule = "─" * 60
    lines = [rule, f"Hamilton · {ph} phase"]

    crit = "criterion" if n_acs == 1 else "criteria"
    if cov is None:
        lines.append(f"{n_reqs} requirement(s), {n_acs} acceptance {crit} "
                     f"· coverage unknown (no .hamilton/config)")
    else:
        covered, uncovered, stale = cov
        tail = f"{covered}/{n_acs} covered"
        if uncovered:
            tail += f" · {uncovered} uncovered"
        if stale:
            tail += f" · {stale} stale"
        lines.append(f"{n_reqs} requirement(s), {n_acs} acceptance {crit} "
                     f"· {tail} · {gate}")

    lines.append("")
    lines.append("Last 3 spec/ changes:")
    lines.extend(f"  {c}" for c in _recent_spec_changes(root))

    if ph == "build":
        pending = _uncommitted_spec(root)
        if pending:
            lines.append("")
            lines.append("Uncommitted spec/ changes to propagate:")
            lines.extend(f"  {p}" for p in pending)

    lines.append(rule)
    return "\n".join(lines)


def main(phase: str | None = None) -> int:
    root = os.getcwd()
    if not os.path.isdir(os.path.join(root, "spec")):
        print("hamilton status: spec/ not found (run from the project root)",
              file=sys.stderr)
        return 2
    print(render(root, phase))
    return 0
