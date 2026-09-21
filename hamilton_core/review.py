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

Up to `PARALLEL` reviewers run at once; the suffixes are written after all
of them have answered, in file and line order.

On a terminal the running reviews show live, each finished one leaves a line,
and what did not pass ends up in a list the engineer unfolds one test at a
time: the criterion's text, then the review. Elsewhere the same text prints
unfolded.

An answer that does not parse is an error: no suffix is written. Runs only in
build phase, because it writes test files. Exit 0 when every reviewed test
passed (or nothing needed review), 1 on any reject, unclear or error, 2 on a
usage error. `--json` emits {"ok": bool, "results": [...]} or {"error": "..."};
each result is {"ac", "criterion", "file", "line", "state", "verdict",
"reasons", "question"}, where `criterion` is the AC's text and `state` says why
the tag was up for review.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import textwrap
import time
from importlib import resources
from string import Template

from hamilton_core import phase as _phase
from hamilton_core.check import (REQ_REL, REVIEWED, TAG_RE, UsageError,
                                 counted, extract, extract_methods,
                                 method_paths, read_config, scan)
from hamilton_core.session import protocol as P
from hamilton_core.session.console import Console

VERDICTS = ("pass", "reject", "unclear")
PARALLEL = 4            # reviewer sessions at once
MARKS = {"pass": "✓", "reject": "✗", "unclear": "?", "error": "!"}
DONE = {"pass": "passed", "reject": "rejected", "unclear": "unclear",
        "error": "error"}
INDENT = "    "
LABEL = 11              # width of the label column in an unfolded result
MAX_WIDTH = 100         # longer lines are hard to read, however wide the terminal
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


class Watch:
    """What `review` reports while it runs. This one ignores it."""

    def started(self, key, label: str) -> None:
        pass

    def finished(self, key, results: list) -> None:
        pass


async def review(root: str, judge, only: str | None = None,
                 watch: Watch | None = None) -> list:
    """Review every target, `PARALLEL` at a time. Returns one result per
    tag, in file and line order."""
    reqs, defined, groups = targets(root, only)
    watch = watch or Watch()
    slots = asyncio.Semaphore(PARALLEL)

    async def judged(key, group) -> list:
        quals = _quals(group)
        async with slots:
            watch.started(key, f"{', '.join(quals)} · "
                               f"{os.path.basename(group[0].tag.file)}:{group[0].tag.line}")
            try:
                verdicts = parse(await judge.ask(prompt(reqs, defined, group)), quals)
            except Exception as exc:      # the judge failed, or answered badly
                verdicts = {q: {"verdict": "error", "reasons": [str(exc)],
                                "question": ""} for q in quals}
        results = [{"ac": _qual(c),
                    "criterion": reqs[c.tag.rid]["acs"][c.tag.acid]["text"],
                    "file": c.tag.file, "line": c.tag.line, "state": c.state,
                    **verdicts[_qual(c)]} for c in group]
        watch.finished(key, results)
        return results

    answers = await asyncio.gather(*(judged(i, g) for i, g in enumerate(groups)))
    for group, results in zip(groups, answers):
        for c, r in zip(group, results):
            if r["verdict"] == "pass":
                write_suffix(root, c.tag, c.want)
    return [r for results in answers for r in results]


# --- what the engineer reads --------------------------------------------------

def line(r: dict, paint) -> str:
    """A result on one line: what finished, and the heading of its details."""
    colour = {"pass": paint.green, "unclear": paint.yellow}.get(r["verdict"], paint.red)
    text = f"{colour(MARKS[r['verdict']])} {paint.bold(r['ac'])}  {r['file']}:{r['line']}"
    if r["verdict"] != "pass":
        text += f"  {colour(DONE[r['verdict']])}"
    return text


def details(r: dict, width: int, paint) -> list:
    """A result unfolded: the criterion, then the review."""
    out = _field("Criterion", r["criterion"], width, paint)
    label = "Error" if r["verdict"] == "error" else "Review"
    out += _field(label, paint.dim(f"({r['state']})"), width, paint)
    for reason in r["reasons"]:
        out += textwrap.wrap(reason, width, initial_indent=INDENT + "  • ",
                             subsequent_indent=INDENT + "    ")
    if r["question"]:
        out += _field("Question", r["question"], width, paint)
    return out


def _field(label: str, text: str, width: int, paint) -> list:
    """``label`` in its column, ``text`` wrapped beside it."""
    body = textwrap.wrap(text, max(width - len(INDENT) - LABEL, 20)) or [""]
    return ([INDENT + paint.bold(label.ljust(LABEL)) + body[0]]
            + [INDENT + " " * LABEL + more for more in body[1:]])


def summary(results: list) -> str:
    if not results:
        return "nothing needed review"
    counts = {v: sum(r["verdict"] == v for r in results) for v in (*VERDICTS, "error")}
    text = f"{len(results)} reviewed · " + " · ".join(
        f"{n} {DONE[v]}" for v, n in counts.items() if n)
    if counts["pass"]:
        text += f". Suffixes written for the {counts['pass']} that passed"
    return text + "."


class _Shown(Watch):
    """The running reviews as rows under the console's indicator, and a line
    for each one that finished."""

    def __init__(self, console: Console) -> None:
        self._console = console
        self._rows: dict = {}
        self.rows: tuple = ()           # read by the indicator's thread

    def started(self, key, label: str) -> None:
        self._rows[key] = P.Activity(str(key), label, time.monotonic())
        self.rows = tuple(self._rows.values())

    def finished(self, key, results: list) -> None:
        self._rows.pop(key, None)
        self.rows = tuple(self._rows.values())
        for r in results:
            self._console.say("  " + line(r, self._console.paint))


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
    # Under --json, stdout is the JSON alone; what a person reads goes to stderr.
    console = Console(out=sys.stderr if as_json else sys.stdout)
    shown = _Shown(console)
    console.follow(lambda: shown.rows)
    if not as_json:
        console.start_working("Reviewing")
    try:
        results = asyncio.run(review(root, judge, only, watch=shown))
    except UsageError as exc:
        return usage(str(exc))
    finally:
        console.stop_working()

    ok = all(r["verdict"] == "pass" for r in results)
    if as_json:
        print(json.dumps({"ok": ok, "results": results}))
        console.say(f"hamilton review: {summary(results)}")
        return 0 if ok else 1
    console.say()
    console.say(summary(results))
    open_ = [r for r in results if r["verdict"] != "pass"]
    if open_:
        width = min(shutil.get_terminal_size().columns - 1, MAX_WIDTH)
        console.say()
        console.say(console.paint.bold(f"{len(open_)} need attention:"))
        console.browse([(line(r, console.paint), details(r, width, console.paint))
                        for r in open_])
    return 0 if ok else 1
