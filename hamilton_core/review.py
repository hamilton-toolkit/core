"""The test reviewer (D-020): the review step of `hamilton build`.

Every tag that counts toward coverage needs a current review suffix (see
`check`). `review` reviews each tag whose suffix is missing or out of date,
and is the only thing that writes one. There is no command of its own: build
drives it until the tests converge, so a separate run would only ever find
nothing to review.

The unit of review is the acceptance criterion. When any of a criterion's
tags needs a review, all of its tests are judged together, wherever they lie:
several tests can share a criterion's cases between them, and only together
do they prove it. A `protocol.Judge` -- a fresh agent session with no tools --
gets exactly the prompt this module builds: the criterion with its
requirement's id, title and Statement, the definitions of the methods its
tests count toward, and every one of its tests -- each file's preamble once,
then the tagged sections. No implementation, no file access. It comes to one
verdict per criterion:

  pass     the suffix is written into each of the criterion's tag lines
           (nothing else in the files changes)
  reject   the file is left alone; the comments are reported
  unclear  the file is left alone; the AC's text cannot settle it, or no test
           by its method could satisfy it -- a spec defect; the question is
           reported for the engineer

The reviewer does not pick the verdict; Hamilton computes it (`verdict`). A
first review returns what the test covers and a list of comments. A re-review
-- the build loop keeps what earlier reviews said -- raises nothing new: it
settles each comment (resolved or not) and each covered point (still covered
or not). A lost point reopens as a comment, a resolved comment becomes a
covered point, so a test's coverage only grows and the list only shrinks.

Up to `PARALLEL` reviewers run at once; the suffixes are written after all
of them have answered. The running reviews show as rows under the build's
indicator (`Shown`), and what did not pass ends up in a list the engineer
unfolds one criterion at a time: the criterion's text, then the review.

An answer that does not parse is an error: no suffix is written. Each result
is one criterion: {"ac", "criterion", "tests", "file", "line", "state",
"verdict", "covered", "comments", "resolved", "question"}, where `criterion`
is the AC's text, `tests` lists every test judged ({"file", "line",
"state"}), `file`/`line` is the first of them and `state` says why the
criterion was up for review, and `comments` are the open points as
{"check", "text", "why"}.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import textwrap
from importlib import resources
from string import Template

from hamilton_core.check import (REQ_REL, REVIEWED, TAG_RE, UsageError,
                                 counted, extract, extract_methods,
                                 method_paths, read_config, scan)
from hamilton_core.session.console import Console, Rows

VERDICTS = ("pass", "reject", "unclear")
PARALLEL = 4            # reviewer sessions at once
MARKS = {"pass": "✓", "reject": "✗", "unclear": "?", "error": "!"}
DONE = {"pass": "passed", "reject": "rejected", "unclear": "unclear",
        "error": "error"}
INDENT = "    "
LABEL = 11              # width of the label column in an unfolded result
MAX_WIDTH = 100         # longer lines are hard to read, however wide the terminal
def targets(root: str) -> tuple[dict, dict, list]:
    """(reqs, defined, groups): one group per acceptance criterion that needs
    a review -- every counting tag of it, wherever it lies, as soon as one of
    them is not reviewed. The criterion is the unit: its tests are judged
    together, because together is how they prove it. Groups come in the
    order of their first test. Raises UsageError when the spec or config is
    missing."""
    if not os.path.isfile(os.path.join(root, REQ_REL)):
        raise UsageError(f"{REQ_REL}: not found (run from the project root)")
    paths = method_paths(read_config(root))
    reqs, _dupes, _malformed = extract(os.path.join(root, REQ_REL))
    defined = extract_methods(os.path.join(root, REQ_REL))
    tags = scan(root, [d for ds in paths.values() for d in ds])
    by_ac: dict = {}
    for c in counted(root, reqs, defined, paths, tags):
        by_ac.setdefault(_qual(c), []).append(c)
    groups = [sorted(g, key=lambda c: (c.tag.file, c.tag.line))
              for g in by_ac.values()
              if any(c.state != REVIEWED for c in g)]
    groups.sort(key=lambda g: (g[0].tag.file, g[0].tag.line))
    return reqs, defined, groups


def _qual(c) -> str:
    return f"{c.tag.rid}/{c.tag.acid}"


def _criterion(reqs: dict, defined: dict, group) -> str:
    c = group[0]
    r = reqs[c.tag.rid]
    title = f' "{r["title"]}"' if r["title"] else ""
    methods = sorted({m for c in group for m in c.methods})
    listed = "\n".join(f"- **{m}** — {defined[m]['description']}" for m in methods)
    return (f"## {_qual(c)}\n\n"
            f"Requirement: {c.tag.rid}{title}\n"
            f"Statement: {r['statement'] or '(none)'}\n"
            f"Criterion: {c.tag.acid}: {r['acs'][c.tag.acid]['text']}\n"
            f"Verified by:\n{listed}")


def _tests(group) -> str:
    """Every test of the criterion, file by file: the file's preamble once,
    then each of its tagged sections. A region is preamble plus section, so
    the preamble is what comes before the region's first tag line."""
    files: dict = {}
    for c in group:
        lines = c.region.splitlines()
        first = next(i for i, ln in enumerate(lines) if TAG_RE.search(ln))
        preamble, section = "\n".join(lines[:first]), "\n".join(lines[first:])
        entry = files.setdefault(c.tag.file, {"preamble": preamble, "sections": {}})
        entry["sections"].setdefault(section, c.tag.line)
    out = []
    for path, entry in files.items():
        out.append(f"### `{path}`\n\nPreamble (everything above its first "
                   f"`@covers` tag):\n\n```\n{entry['preamble']}\n```")
        for section, line in entry["sections"].items():
            out.append(f"Test at line {line}:\n\n```\n{section}\n```")
    return "\n\n".join(out)


def points(earlier: dict) -> tuple[dict, dict]:
    """The earlier review's points, numbered by Hamilton: ({K1: covered},
    {C1: comment}). The reviewer settles them by these ids and cannot add to
    them."""
    return ({f"K{i}": text for i, text in enumerate(earlier["covered"], 1)},
            {f"C{i}": c["text"] for i, c in enumerate(earlier["comments"], 1)})


def prompt(reqs: dict, defined: dict, group, earlier: dict | None = None) -> str:
    """The whole of what the reviewer sees for one criterion: the criterion,
    and all of its tests. With `earlier` -- the criterion's last review -- it
    is a re-review: the same, plus the points to settle."""
    block = _criterion(reqs, defined, group)
    if earlier:
        kept, comments = points(earlier)
        block += ("\n\nCovered before:\n"
                  + ("\n".join(f"- {k}: {t}" for k, t in kept.items())
                     or "- (nothing)")
                  + "\n\nComments to settle:\n"
                  + ("\n".join(f"- {k}: {t}" for k, t in comments.items())
                     or "- (none)"))
    name = "prompts/rereview.md" if earlier else "prompts/review.md"
    template = resources.files("hamilton_core").joinpath(name)
    return Template(template.read_text(encoding="utf-8")).substitute(
        criteria=block, tests=_tests(group))


def _items(reply: str, quals) -> dict:
    """{qual: the object the reviewer answered for it}. Raises ValueError
    when the answer is not a JSON array holding one object per qual."""
    text = reply.strip()
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        raise ValueError("the answer holds no JSON array")
    try:
        items = json.loads(text[start:end + 1])
    except ValueError as exc:
        raise ValueError(f"the answer is not valid JSON ({exc})") from None
    if not isinstance(items, list):
        raise ValueError("the answer is not a JSON array")
    out = {item["ac"]: item for item in items
           if isinstance(item, dict) and item.get("ac") in quals}
    missing = [q for q in quals if q not in out]
    if missing:
        raise ValueError(f"no answer for {', '.join(missing)}")
    return out


def _question(item: dict) -> str:
    question = item.get("question") or ""
    if not isinstance(question, str):
        raise ValueError(f"malformed question for {item['ac']}")
    return question


def parse_first(reply: str, quals) -> dict:
    """{qual: {"covered", "comments", "question"}} from a first review."""
    out = {}
    for qual, item in _items(reply, quals).items():
        # Both lists are required: an answer without them is not an empty
        # review, and must never read as a pass.
        covered, comments = item.get("covered"), item.get("comments")
        if not isinstance(covered, list) or not isinstance(comments, list):
            raise ValueError(f"malformed review for {qual}: {item!r}")
        out[qual] = {
            "covered": [str(c) for c in covered],
            "comments": [{"check": str(c.get("check", "")), "text": str(c["text"])}
                         if isinstance(c, dict) and "text" in c
                         else {"check": "", "text": str(c)} for c in comments],
            "question": _question(item)}
    return out


def parse_settle(reply: str, earlier: dict) -> dict:
    """{qual: {"kept", "resolved", "question"}} from a re-review. Every earlier
    point must be settled, and none added: a re-review that goes beyond its
    list is not a re-review."""
    out = {}
    for qual, item in _items(reply, list(earlier)).items():
        kept_ids, comment_ids = (set(ids) for ids in points(earlier[qual]))
        kept, resolved = item.get("kept") or {}, item.get("resolved") or {}
        if not isinstance(kept, dict) or not isinstance(resolved, dict):
            raise ValueError(f"malformed re-review for {qual}: {item!r}")
        for ids, answered, what in ((kept_ids, kept, "covered point"),
                                    (comment_ids, resolved, "comment")):
            if set(answered) - ids:
                raise ValueError(f"the re-review of {qual} adds a {what}: "
                                 f"{', '.join(sorted(set(answered) - ids))}")
            if ids - set(answered):
                raise ValueError(f"the re-review of {qual} leaves a {what} "
                                 f"unsettled: {', '.join(sorted(ids - set(answered)))}")
            for pid, a in answered.items():
                if not isinstance(a, dict) or not isinstance(a.get("ok"), bool):
                    raise ValueError(f"malformed answer for {qual} {pid}: {a!r}")
        out[qual] = {"kept": kept, "resolved": resolved, "question": _question(item)}
    return out


def verdict(comments: list, question: str) -> str:
    """Hamilton's call, not the model's: a question makes it unclear, an open
    comment a reject, nothing open a pass."""
    return "unclear" if question else ("reject" if comments else "pass")


def settle(earlier: dict, answer: dict) -> dict:
    """A re-review, applied. Unresolved comments stay open; a covered point
    the revision lost reopens as a comment; a resolved comment becomes a
    covered point -- so what a test covers can only grow."""
    kept_pts, comment_pts = points(earlier)
    covered = [text for pid, text in kept_pts.items() if answer["kept"][pid]["ok"]]
    # A comment keeps its text round after round; only `why` -- the latest
    # reason it is still open -- changes.
    comments = [{"check": "coverage", "text": f"no longer covers: {text}",
                 "why": str(answer["kept"][pid].get("why") or "")}
                for pid, text in kept_pts.items() if not answer["kept"][pid]["ok"]]
    resolved = []
    for (pid, text), c in zip(comment_pts.items(), earlier["comments"]):
        a = answer["resolved"][pid]
        if a["ok"]:
            resolved.append(text)
            covered.append(str(a.get("covers") or text))
        else:
            comments.append(dict(c, why=str(a.get("why") or "")))
    # Only the engineer's answer settles a question, and that answer changes
    # the criterion, which forgets this review. Until then it stays unclear.
    question = answer["question"] or earlier.get("question", "")
    return {"covered": covered, "comments": comments, "resolved": resolved,
            "question": question, "verdict": verdict(comments, question)}


def remember(memory: dict, results: list) -> dict:
    """The memory after `results`, one entry per criterion: a criterion that
    passed is forgotten, one that did not keeps what its tests cover, what is
    still open and the question it raised. An error changes nothing -- it is
    not a review."""
    out = dict(memory)
    for r in results:
        if r["verdict"] == "pass":
            out.pop(r["ac"], None)
        elif r["verdict"] != "error":
            out[r["ac"]] = {"covered": r["covered"], "comments": r["comments"]}
            if r["question"]:
                out[r["ac"]]["question"] = r["question"]
    return out


def forget(memory: dict, quals) -> dict:
    """The memory without these criteria -- their wording changed, so what
    was said about their tests no longer holds."""
    return {q: v for q, v in memory.items() if q not in set(quals)}


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


async def review(root: str, judge, watch: Watch | None = None,
                 memory: dict | None = None) -> list:
    """Review every criterion that needs it, `PARALLEL` at a time. Returns
    one result per criterion, in the order of their first test.

    `memory` is what earlier reviews said ({qual: covered and comments}). A
    criterion it knows is re-reviewed: its points are settled, and nothing
    new is raised. One it does not know gets a first review against the
    spec. A pass writes the suffix of every one of the criterion's tags."""
    reqs, defined, groups = targets(root)
    memory = memory or {}
    watch = watch or Watch()
    slots = asyncio.Semaphore(PARALLEL)

    async def judged(key, group) -> dict:
        qual = _qual(group[0])
        earlier = memory.get(qual)
        if earlier and not (earlier["covered"] or earlier["comments"]
                            or earlier.get("question")):
            earlier = None      # nothing to settle: a re-review would pass it unseen
        async with slots:
            files = sorted({os.path.basename(c.tag.file) for c in group})
            watch.started(key, f"{qual} · {', '.join(files)}")
            try:
                reply = await judge.ask(prompt(reqs, defined, group, earlier))
                if earlier:
                    answer = settle(earlier, parse_settle(reply, {qual: earlier})[qual])
                else:
                    a = parse_first(reply, [qual])[qual]
                    answer = dict(a, resolved=[],
                                  verdict=verdict(a["comments"], a["question"]))
            except Exception as exc:      # the judge failed, or answered badly
                answer = {"verdict": "error", "covered": [], "resolved": [],
                          "comments": [{"check": "error", "text": str(exc)}],
                          "question": ""}
        c = group[0]
        open_ = next((t for t in group if t.state != REVIEWED), c)
        result = {"ac": qual,
                  "criterion": reqs[c.tag.rid]["acs"][c.tag.acid]["text"],
                  "tests": [{"file": t.tag.file, "line": t.tag.line,
                             "state": t.state} for t in group],
                  "file": c.tag.file, "line": c.tag.line, "state": open_.state,
                  **answer}
        watch.finished(key, [result])
        return result

    results = await asyncio.gather(*(judged(i, g) for i, g in enumerate(groups)))
    for group, r in zip(groups, results):
        if r["verdict"] == "pass":
            for c in group:
                if c.state != REVIEWED:
                    write_suffix(root, c.tag, c.want)
    return list(results)


# --- what the engineer reads --------------------------------------------------

def line(r: dict, paint) -> str:
    """A result on one line: what finished, and the heading of its details."""
    colour = {"pass": paint.green, "unclear": paint.yellow}.get(r["verdict"], paint.red)
    more = len(r.get("tests", ())) - 1
    where = f"{r['file']}:{r['line']}" + (f" +{more} more" if more > 0 else "")
    text = f"{colour(MARKS[r['verdict']])} {paint.bold(r['ac'])}  {where}"
    if r["verdict"] != "pass":
        text += f"  {colour(DONE[r['verdict']])}"
    return text


def details(r: dict, width: int, paint) -> list:
    """A result unfolded: the criterion, then the review."""
    out = _field("Criterion", r["criterion"], width, paint)
    label = "Error" if r["verdict"] == "error" else "Review"
    out += _field(label, paint.dim(f"({r['state']})"), width, paint)
    for comment in r["comments"]:
        out += textwrap.wrap(comment["text"], width, initial_indent=INDENT + "  • ",
                             subsequent_indent=INDENT + "    ")
        if comment.get("why"):
            out += [paint.dim(ln) for ln in textwrap.wrap(
                f"still open: {comment['why']}", width,
                initial_indent=INDENT + "    ", subsequent_indent=INDENT + "    ")]
    for done in r.get("resolved", ()):
        out += [paint.dim(ln) for ln in textwrap.wrap(
            f"resolved: {done}", width, initial_indent=INDENT + "  ✓ ",
            subsequent_indent=INDENT + "    ")]
    if r["question"]:
        out += _field("Question", r["question"], width, paint)
    return out


def _field(label: str, text: str, width: int, paint) -> list:
    """``label`` in its column, ``text`` wrapped beside it."""
    body = textwrap.wrap(text, max(width - len(INDENT) - LABEL, 20)) or [""]
    return ([INDENT + paint.bold(label.ljust(LABEL)) + body[0]]
            + [INDENT + " " * LABEL + more for more in body[1:]])


def grouped(results: list, paint) -> list:
    """A review as a handful of lines: what passed, then each file with what
    it has open. Forty-odd one-line verdicts do not scan, and the reasons are
    a keystroke away in the list that follows."""
    out = []
    passed = sum(r["verdict"] == "pass" for r in results)
    if passed:
        out.append(paint.green(f"✓ {passed} passed"))
    for path in dict.fromkeys(r["file"] for r in results if r["verdict"] != "pass"):
        counts: dict = {}
        for r in results:
            if r["file"] == path and r["verdict"] != "pass":
                counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        only_unclear = set(counts) == {"unclear"}
        colour = paint.yellow if only_unclear else paint.red
        mark = MARKS["unclear"] if only_unclear else MARKS["reject"]
        out.append(f"{colour(mark)} {path}  "
                   + " · ".join(f"{n} {DONE[v]}" for v, n in counts.items()))
    return out


def browse(console: Console, results: list) -> None:
    """What did not pass, as a list the engineer unfolds one test at a time."""
    open_ = [r for r in results if r["verdict"] != "pass"]
    if not open_:
        return
    width = min(shutil.get_terminal_size().columns - 1, MAX_WIDTH)
    console.say()
    console.say(console.paint.bold(f"{len(open_)} need attention:"))
    console.browse([(line(r, console.paint), details(r, width, console.paint))
                    for r in open_])


def summary(results: list) -> str:
    if not results:
        return "nothing needed review"
    counts = {v: sum(r["verdict"] == v for r in results) for v in (*VERDICTS, "error")}
    text = f"{len(results)} reviewed · " + " · ".join(
        f"{n} {DONE[v]}" for v, n in counts.items() if n)
    if counts["pass"]:
        text += f". Suffixes written for the {counts['pass']} that passed"
    return text + "."


class Shown(Watch):
    """The running reviews as rows under the console's indicator. They are
    one step of a longer run, so they report as a few grouped lines at the
    end rather than one line per criterion as they land."""

    def __init__(self, console: Console, rows: Rows | None = None) -> None:
        self._console = console
        self._open: list = []           # what has not passed, to unfold later
        # `hamilton build` draws its own steps and the reviews in one place,
        # so it hands its rows in rather than keeping a second set.
        self.rows = rows or Rows()

    def started(self, key, label: str) -> None:
        self.rows.start(key, label)

    def finished(self, key, results: list) -> None:
        self.rows.stop(key)

    def report(self, results: list) -> None:
        """The review as the engineer reads it: the files with something open,
        the summary, and unfolded whatever stops the loop -- an `unclear` or
        an `error` is about to become a hard stop, and its question is what
        they have to act on."""
        for text in grouped(results, self._console.paint):
            self._console.say("  " + text)
        self._console.say("  " + summary(results))
        width = min(shutil.get_terminal_size().columns - 1, MAX_WIDTH)
        for r in results:
            if r["verdict"] in ("unclear", "error"):
                self._console.say()
                self._console.say("  " + line(r, self._console.paint))
                for text in details(r, width, self._console.paint):
                    self._console.say(text)
        # What is still open, not what ever was: a reject the writer has since
        # fixed is not something to hand the engineer at the end.
        self._open = [r for r in results if r["verdict"] != "pass"]

    def browse(self) -> None:
        """The open results, once the run is over. Shown once:
        after that they are in the scrollback as the engineer left them."""
        open_, self._open = self._open, []
        browse(self._console, open_)

    def failed(self, message: str) -> None:
        self._console.say("  " + self._console.paint.red(f"review: {message}"))

