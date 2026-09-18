"""`hamilton review [R-nnnn/ACn] [--json]` -- the test reviewer (D-020).

Every tag that counts toward coverage needs a current review suffix (see
`check`). This command reviews each tag whose suffix is missing or out of
date -- or only those for one AC -- and is the only thing that writes one.

The tags are grouped by region: one test, possibly tagged with several ACs.
For each region a `protocol.Judge` -- a fresh agent session with no tools --
gets exactly the prompt this module builds: each AC with its requirement's id,
title and Statement, the definitions of the methods the test counts toward,
and the region text with its file path. No implementation, no file access.
It answers one verdict per AC:

  pass     the suffix is written into that tag line (nothing else in the
           file changes)
  reject   the file is left alone; the reasons are reported
  unclear  the file is left alone; the AC's text cannot settle it, which is a
           spec defect -- the question is reported for the engineer

An answer that does not parse is an error: no suffix is written. Runs only in
build phase, because it writes test files. Exit 0 when every reviewed test
passed (or nothing needed review), 1 on any reject, unclear or error, 2 on a
usage error. `--json` emits {"ok": bool, "results": [...]} or {"error": "..."};
each result is {"ac", "file", "line", "state", "verdict", "reasons",
"question"}, where `state` says why the tag was up for review.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from importlib import resources
from string import Template

from hamilton_core import phase as _phase
from hamilton_core.check import (REQ_REL, REVIEWED, TAG_RE, UsageError,
                                 counted, extract, extract_methods,
                                 method_paths, read_config, scan)

VERDICTS = ("pass", "reject", "unclear")
_QUAL_RE = re.compile(r"(R-\d{4})/(AC\d+)")


def targets(root: str, only: str | None = None) -> tuple[dict, dict, list]:
    """(reqs, defined, groups): the counting tags that need a review, grouped
    by the test region they share, in file and line order. ``only`` limits
    them to one "R-nnnn/ACn". Raises UsageError when the spec or config is
    missing, or ``only`` names no AC."""
    if not os.path.isfile(os.path.join(root, REQ_REL)):
        raise UsageError(f"{REQ_REL}: not found (run from the project root)")
    paths = method_paths(read_config(root))
    reqs, _dupes, _malformed = extract(os.path.join(root, REQ_REL))
    defined = extract_methods(os.path.join(root, REQ_REL))
    if only is not None:
        m = _QUAL_RE.fullmatch(only)
        if not m:
            raise UsageError(f"{only!r} is not an acceptance criterion id; "
                             f"give it as R-nnnn/ACn, e.g. R-0001/AC2")
        if m.group(2) not in reqs.get(m.group(1), {}).get("acs", {}):
            raise UsageError(f"{only} is not declared in {REQ_REL}")
    tags = scan(root, [d for ds in paths.values() for d in ds])
    groups: dict = {}
    for c in counted(root, reqs, defined, paths, tags):
        if c.state == REVIEWED:
            continue
        if only is not None and f"{c.tag.rid}/{c.tag.acid}" != only:
            continue
        groups.setdefault((c.tag.file, c.region), []).append(c)
    ordered = sorted(groups.values(), key=lambda g: (g[0].tag.file, g[0].tag.line))
    return reqs, defined, ordered


def _qual(c) -> str:
    return f"{c.tag.rid}/{c.tag.acid}"


def _quals(group) -> list:
    """The distinct ACs of a region, in tag order."""
    return list(dict.fromkeys(_qual(c) for c in group))


def prompt(reqs: dict, defined: dict, group) -> str:
    """The whole of what the reviewer sees for one region."""
    blocks = []
    for qual in _quals(group):
        c = next(c for c in group if _qual(c) == qual)
        r = reqs[c.tag.rid]
        title = f' "{r["title"]}"' if r["title"] else ""
        methods = "\n".join(f"- **{m}** — {defined[m]['description']}"
                            for m in c.methods)
        blocks.append(
            f"## {qual}\n\n"
            f"Requirement: {c.tag.rid}{title}\n"
            f"Statement: {r['statement'] or '(none)'}\n"
            f"Criterion: {c.tag.acid}: {r['acs'][c.tag.acid]['text']}\n"
            f"Verified by:\n{methods}")
    template = resources.files("hamilton_core").joinpath("prompts/review.md")
    return Template(template.read_text(encoding="utf-8")).substitute(
        criteria="\n\n".join(blocks), file=group[0].tag.file,
        region=group[0].region)


def parse(reply: str, quals) -> dict:
    """{qual: {"verdict", "reasons", "question"}} from the reviewer's answer.
    Raises ValueError when it is not a JSON array holding one well-formed
    verdict for each of ``quals``."""
    text = reply.strip()
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        raise ValueError("the answer holds no JSON array")
    try:
        items = json.loads(text[start:end + 1])
    except ValueError as exc:
        raise ValueError(f"the answer is not valid JSON ({exc})") from None
    out = {}
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, dict) or item.get("ac") not in quals:
            continue
        verdict = item.get("verdict")
        reasons = item.get("reasons") or []
        question = item.get("question") or ""
        if (verdict not in VERDICTS or not isinstance(reasons, list)
                or not isinstance(question, str)):
            raise ValueError(f"malformed verdict for {item['ac']}: {item!r}")
        out[item["ac"]] = {"verdict": verdict,
                           "reasons": [str(r) for r in reasons],
                           "question": question}
    missing = [q for q in quals if q not in out]
    if missing:
        raise ValueError(f"no verdict for {', '.join(missing)}")
    return out


def write_suffix(root: str, tag, value: str) -> None:
    """Set the review suffix of ``tag`` to ``value``. Only that tag on that
    line changes; line endings and everything else in the file are kept."""
    path = os.path.join(root, tag.file)
    with open(path, encoding="utf-8", newline="") as fh:
        parts = re.split(r"(\r\n|\r|\n)", fh.read())

    def stamp(m):
        if (m.group(1), m.group(2)) != (tag.rid, tag.acid):
            return m.group(0)
        if m.group(3) is None:
            return f"{m.group(0)} #{value}"
        return m.group(0)[:m.start(3) - m.start()] + value

    i = 2 * (tag.line - 1)          # parts alternate: line, ending, line, ...
    parts[i] = TAG_RE.sub(stamp, parts[i])
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("".join(parts))


async def review(root: str, judge, only: str | None = None,
                 progress=lambda msg: None) -> list:
    """Review every target, sequentially. Returns one result per tag."""
    reqs, defined, groups = targets(root, only)
    results = []
    for group in groups:
        quals = _quals(group)
        progress(f"reviewing {group[0].tag.file}:{group[0].tag.line} "
                 f"({', '.join(quals)})")
        try:
            verdicts = parse(await judge.ask(prompt(reqs, defined, group)), quals)
        except Exception as exc:      # the judge failed, or answered badly
            verdicts = {q: {"verdict": "error", "reasons": [str(exc)],
                            "question": ""} for q in quals}
        for c in group:
            v = verdicts[_qual(c)]
            if v["verdict"] == "pass":
                write_suffix(root, c.tag, c.want)
            results.append({"ac": _qual(c), "file": c.tag.file,
                            "line": c.tag.line, "state": c.state, **v})
    return results


def _render(results: list) -> list:
    lines = []
    for r in results:
        head = f"{r['file']}:{r['line']}: {r['ac']}: {r['verdict']} ({r['state']})"
        if r["verdict"] == "pass":
            head += " -- suffix written"
        lines.append(head)
        lines += [f"  - {reason}" for reason in r["reasons"]]
        if r["question"]:
            lines.append(f"  question: {r['question']}")
    return lines


def main(only: str | None = None, as_json: bool = False, judge=None) -> int:
    root = os.getcwd()

    def usage(msg: str) -> int:
        if as_json:
            print(json.dumps({"error": msg}))
        else:
            print(f"hamilton review: {msg}", file=sys.stderr)
        return 2

    phase = _phase.read(root)
    if phase != "build":
        return usage(f"phase is {phase or 'unset'!r}, not 'build'. Review "
                     f"writes suffixes into test files, which only build phase "
                     f"may write. Run it inside `hamilton build`.")
    if judge is None:
        from hamilton_core.session.claude_sdk_adapter import ClaudeSdkJudge
        judge = ClaudeSdkJudge()
    try:
        results = asyncio.run(review(
            root, judge, only,
            progress=lambda msg: print(f"hamilton review: {msg}", file=sys.stderr)))
    except UsageError as exc:
        return usage(str(exc))

    ok = all(r["verdict"] == "pass" for r in results)
    if as_json:
        print(json.dumps({"ok": ok, "results": results}))
    else:
        for line in _render(results):
            print(line)
        counts = {v: sum(r["verdict"] == v for r in results)
                  for v in (*VERDICTS, "error")}
        summary = (", ".join(f"{n} {v}" for v, n in counts.items() if n)
                   or "nothing needed review")
        print(f"hamilton review: {len(results)} tag(s): {summary}", file=sys.stderr)
    return 0 if ok else 1
