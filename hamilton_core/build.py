"""`hamilton build` -- the loop that gets the gate green.

Build is not a conversation, and it is not an agent left to its own judgement:
what to do next follows from `hamilton verify`. So Hamilton drives, and calls
an agent only for the work that needs one.

    check   Hamilton runs the gate in-process. Its findings are the work list,
            routed by `route()` -- one rule, one step, written once.
    plan    one task: settle how each criterion is reached (route, command,
            signature), scaffold only what a test writer must be able to
            reach, and set the config keys build phase may set.
    tests   one task per criterion, in parallel, each blind to the
            implementation: its brief is all it gets.
    review  Hamilton runs `review` in-process. A reject goes back to a fresh
            writer with the reviewer's reasons, at most `ROUNDS` times.
    code    one task: make the suite green. Then round again.

Because Hamilton starts each task and waits for it, it always knows what is
running: the step line, the indicator and the rows are what is happening, not
a guess at it. Nothing asks the engineer anything until the loop **cannot
proceed** -- a criterion the reviewer cannot judge, a test still rejected
after its rounds, a suite that stays red -- and then it asks exactly what to
do about that (`Stop`). Without a terminal it reports and ends instead.

What a run keeps in `.hamilton/build` is only what re-running cannot work out
for itself: the criteria the engineer chose to skip, and how many rewrite
rounds each test has had. Everything else is read back out of `hamilton
check`, which is why an interrupted run needs no resuming -- just run it
again.

Written against `protocol` alone: the SDK is imported in `main`, so the loop
itself is driven by fakes in the tests.

Exit: 0 when the gate is green, 1 when it stopped, 2 outside a project or in
the wrong phase, 130 if interrupted.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from importlib import resources
from string import Template

from hamilton_core import verify as _verify
from hamilton_core import clarify as _clarify
from hamilton_core import guard as _guard
from hamilton_core import phase as _phase
from hamilton_core import review as _review
from hamilton_core import status as _status
from hamilton_core.verify import REQ_REL, UsageError
from hamilton_core.session import protocol as P
from hamilton_core.session.console import Console, Rows, elapsed

STATE_REL = os.path.join(".hamilton", "build")
MODEL_PREFIX = "model."
_QUAL_RE = re.compile(r"R-\d{4}/AC\d+")
SESSION_ENV = "HAMILTON_SESSION"

PASSES = 5              # times round the loop before it gives up
ROUNDS = 3              # rewrites of one test after a reject
WRITERS = 4             # test writers at once

# What the time at the end of a run is booked under: each step's kind, and
# the engineer's own time at a question, which is no step's.
SPENT = {"check": "checking", "plan": "planning", "tests": "writing tests",
         "review": "reviewing", "code": "coding", "wait": "waiting for you"}

STEPS = {
    "check": "Checking the gate",
    "plan": "Planning",
    "tests": "Writing tests",
    "retests": "Revising tests",
    "review": "Reviewing tests",
    "code": "Coding",
}

# Every rule `hamilton verify` can report belongs to exactly one step. This is
# the loop's whole decision: keep it here, and nowhere else.
SPEC_RULES = frozenset({"malformed", "dangling-ref", "orphan-requirement",
                        "cyclic-parent", "no-method", "unknown-method",
                        "missing-reference"})
CONFIG_RULES = frozenset({"no-test-command", "no-method-paths", "retired-config"})
COVER_RULES = frozenset({"uncovered", "wrong-method", "orphan-tag"})
SUITE_RULES = frozenset({"tests-failed"})
# The states of an `unreviewed` tag whose criterion changed. Its tests are
# still reviewed first -- most still prove the new wording, and a review is
# far cheaper than a rewrite -- but not against what was said of them before.
CHANGED_STATES = frozenset({_verify.AC_CHANGED, _verify.BOTH_CHANGED})


def qual_of(finding: dict) -> str:
    """The "R-nnnn/ACn" a finding is about, or "" if it is about no one AC."""
    return (f"{finding['req']}/{finding['ac']}"
            if finding.get("req") and finding.get("ac") else "")


@dataclass
class Work:
    """One pass's findings, routed to the step that answers them."""
    spec: list = field(default_factory=list)        # build cannot fix these
    config: list = field(default_factory=list)
    cover: list = field(default_factory=list)       # needs a test written
    review: list = field(default_factory=list)      # needs judging only
    suite: list = field(default_factory=list)
    skipped: list = field(default_factory=list)     # left alone by choice

    @property
    def to_write(self) -> list:
        """The criteria a writer takes this pass, in spec order."""
        return list(dict.fromkeys(q for q in (qual_of(f) for f in self.cover) if q))

    @property
    def open(self) -> bool:
        return bool(self.config or self.cover or self.review or self.suite)


def route(findings: list, skipped) -> Work:
    """The routing table, applied. A criterion the engineer skipped is set
    aside here, so no later step has to remember the decision."""
    work = Work()
    for f in findings:
        if qual_of(f) and qual_of(f) in skipped:
            work.skipped.append(f)
        elif f["rule"] in SPEC_RULES:
            work.spec.append(f)
        elif f["rule"] in CONFIG_RULES:
            work.config.append(f)
        elif f["rule"] in COVER_RULES:
            work.cover.append(f)
        elif f["rule"] in SUITE_RULES:
            work.suite.append(f)
        else:           # `unreviewed`, and any rule added since: judge first
            work.review.append(f)
    return work


# --- what the run remembers ---------------------------------------------------

@dataclass
class State:
    """`.hamilton/build`. Only what a re-run could not work out for itself."""
    skipped: list = field(default_factory=list)
    rounds: dict = field(default_factory=dict)
    # What the last review said about each test that has not passed yet
    # (`review.remember`): the list its next review settles.
    reviews: dict = field(default_factory=dict)
    stopped: str = ""

    @property
    def started(self) -> bool:
        return bool(self.skipped or self.rounds or self.reviews)

    def skip(self, qual: str) -> None:
        if qual not in self.skipped:
            self.skipped.append(qual)

    def round_of(self, qual: str) -> int:
        return int(self.rounds.get(qual, 0))

    def rewrote(self, qual: str) -> None:
        self.rounds[qual] = self.round_of(qual) + 1

    def save(self, root: str) -> None:
        path = os.path.join(root, STATE_REL)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, indent=2, sort_keys=True)
            fh.write("\n")

    @classmethod
    def load(cls, root: str) -> "State":
        """What the last run left, or a fresh state. A file we cannot read is
        no reason to refuse to build."""
        try:
            with open(os.path.join(root, STATE_REL), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        state = cls(**{k: v for k, v in data.items() if k in known})
        # Reviews are kept per criterion, with their advice apart from the
        # blocking comments. An entry under any other key, or one without
        # advice, comes from an earlier layout: its comments were all
        # blocking, and are judged again rather than settled.
        state.reviews = {q: v for q, v in (state.reviews or {}).items()
                         if _QUAL_RE.fullmatch(q) and isinstance(v, dict)
                         and "advice" in v}
        return state

    @classmethod
    def clear(cls, root: str) -> None:
        try:
            os.remove(os.path.join(root, STATE_REL))
        except OSError:
            pass


# --- where the loop stops -----------------------------------------------------

class Failed(RuntimeError):
    """A task did not come back. Its step can be tried again."""

    def __init__(self, label: str, cause: Exception) -> None:
        super().__init__(f"{label}: {cause}")
        self.label = label


class Answer(enum.Enum):
    CLARIFY = enum.auto()
    RETRY = enum.auto()
    SKIP = enum.auto()
    END = enum.auto()


@dataclass
class Stop:
    """Why the loop cannot go on, and what the engineer may do about it."""
    headline: str
    quals: list = field(default_factory=list)
    lines: list = field(default_factory=list)       # already rendered
    retry: str = ""                                 # the retry row's label
    clarify: bool = False                           # offer to clarify the spec


async def _human(run: "Run", ask, *args):
    """A question to the engineer, off the event loop -- the picker runs its
    own, and they take as long as they take. That time is theirs, not the
    step's, so it is booked apart."""
    started = time.monotonic()
    try:
        return await asyncio.to_thread(ask, *args)
    finally:
        run.waited(time.monotonic() - started)


async def _ask(run: "Run", stop: Stop) -> Answer:
    return await _human(run, resolve, run.console, stop, run.state)


def resolve(console: Console, stop: Stop, state: State) -> Answer:
    """Report the stop and ask what to do. Without a terminal there is nobody
    to ask: it is reported and the run ends."""
    console.say()
    console.say(console.paint.heading(f"── {stop.headline} ──"))
    for text in stop.lines:
        console.say(text)
    if not console.interactive:
        return Answer.END

    rows, actions = [], []
    if stop.clarify:
        rows.append(P.Choice("Clarify it", "your answer goes into the spec"))
        actions.append(Answer.CLARIFY)
    if stop.retry:
        rows.append(P.Choice(stop.retry))
        actions.append(Answer.RETRY)
    if stop.quals:
        what = stop.quals[0] if len(stop.quals) == 1 else f"{len(stop.quals)} criteria"
        rows.append(P.Choice(f"Skip {what} and carry on with the rest"))
        actions.append(Answer.SKIP)
    if not rows:
        return Answer.END
    answer = console.choose(P.Question("What now?", tuple(rows)),
                            finish="End the run")
    if answer is None:
        return Answer.END
    chosen = actions[[c.label for c in rows].index(answer)]
    if chosen is Answer.SKIP:
        for qual in stop.quals:
            state.skip(qual)
    return chosen


# --- the prompts --------------------------------------------------------------

def _template(name: str) -> Template:
    return Template(resources.files("hamilton_core")
                    .joinpath(f"prompts/{name}.md").read_text(encoding="utf-8"))


def spec_of(reqs: dict, defined: dict, qual: str) -> str:
    """One criterion as a task sees it: its requirement, what the requirement
    promises, the criterion itself, the definition of each method it is
    verified by, and the spec files it references -- by path: a task can
    read them."""
    rid, acid = qual.split("/")
    req = reqs.get(rid) or {"title": "", "statement": "", "acs": {}}
    ac = req["acs"].get(acid) or {"text": "", "methods": []}
    methods = "\n".join(f"- **{m}** — {defined[m]['description']}"
                        for m in ac.get("methods", ()) if m in defined)
    title = f' "{req["title"]}"' if req.get("title") else ""
    refs = _verify.refs_in(f"{req.get('statement') or ''} {ac['text']}")
    referenced = (f"\nReferences: {', '.join(refs)} -- part of the criterion; "
                  f"read them" if refs else "")
    return (f"Requirement: {rid}{title}\n"
            f"Statement: {req.get('statement') or '(none)'}\n"
            f"Criterion: {acid}: {ac['text']}\n"
            f"Verified by:\n{methods or '- (none declared)'}{referenced}")


def plan_prompt(root: str, work: Work, reqs: dict, defined: dict) -> str:
    quals = work.to_write
    return _template("plan").substitute(
        findings="\n".join(f"- {f['message']}" for f in work.config + work.cover),
        criteria="\n\n".join(spec_of(reqs, defined, q) for q in quals),
        quals=", ".join(quals) or "(none)",
        config=_config_text(root),
        requirements=REQ_REL)


def test_prompt(qual: str, reqs: dict, defined: dict, brief: str,
                paths: dict, review: dict | None, command: str = "") -> str:
    """`review` is the criterion's last review when it did not pass: its
    tests, what they cover, and what is still open. The writer works on the
    criterion as a whole -- the reviewer judges its tests together."""
    rid, acid = qual.split("/")
    methods = (reqs.get(rid, {}).get("acs", {}).get(acid, {}) or {}).get("methods", ())
    where = ", ".join(sorted({d for m in methods for d in paths.get(m, ())}))
    said = ""
    if review:
        said = ("Its tests now: "
                + ", ".join(f"{t['file']}:{t['line']}" for t in review["tests"])
                + "\n\nOpen comments -- solve each:\n"
                + "\n".join(f"- {c['text']}"
                            + (f"\n  still open because: {c['why']}" if c.get("why") else "")
                            for c in review["comments"]))
        if review["covered"]:
            said += ("\n\nAlready covered -- keep every one of these:\n"
                     + "\n".join(f"- {k}" for k in review["covered"]))
        said += ("\n\nThe next review checks only these points, across all of "
                 "the criterion's tests together: that each comment is solved, "
                 "and that nothing already covered was lost. You may add, split "
                 "or merge tests to get there. Change nothing the comments do "
                 "not ask for.")
    return _template("write_test").substitute(
        qual=qual, name=qual.replace("/", "-"), command=command or "the full suite",
        criterion=spec_of(reqs, defined, qual),
        brief=brief or "(none given -- work it out from the criterion)",
        paths=where or "(no path configured for this method)",
        reasons=said or "(this is the first attempt)")


def failing(work: Work) -> list:
    """The criteria whose tests a failed suite names."""
    return [q for f in work.suite for q in f.get("failed", ())]


def rejected_by_criterion(results: list, skipped) -> dict:
    """{qual: its review} for each criterion whose tests were rejected. An
    error is not among them: there is nothing in it for a writer to solve."""
    return {r["ac"]: r for r in results
            if r["verdict"] == "reject" and r["ac"] not in skipped}


def test_files(root: str, quals) -> list:
    """The files holding a `@covers` tag for any of these criteria -- what a
    step working on them runs, instead of the whole suite."""
    return sorted(set().union(*_verify.tagged_files(root, quals).values()))


def code_prompt(work: Work, reqs: dict, defined: dict, quals: list,
                files: list = (), command: str = "") -> str:
    """The implementer's brief. A failed suite comes with its failures: the
    engineer never needs to read them, the implementer does."""
    failures = "\n\n".join(
        f"The failures in the suite's output (the whole of it: {f.get('log') or 'not kept'}):"
        f"\n\n```\n{f['output']}\n```\n\n"
        + (f"The criteria whose tests failed: {', '.join(f['failed'])}. Run "
           f"`hamilton verify` on each until it passes."
           if f.get("failed") else
           "The failures name no criterion's test file: find them in the "
           "output above.")
        for f in work.suite if f.get("output"))
    return _template("implement").substitute(
        findings="\n".join(f"- {f['message']}" for f in work.suite + work.cover)
                 or "- (none: the tests are written and reviewed)",
        failures=failures or "(the suite did not fail)",
        tests="\n".join(f"- {f}" for f in files)
              or "- (none tagged yet: run the failing tests the output above names)",
        command=command or "the full suite",
        criteria="\n\n".join(spec_of(reqs, defined, q) for q in quals) or "(none)")


def _config_text(root: str) -> str:
    try:
        with open(os.path.join(root, ".hamilton", "config"), encoding="utf-8") as fh:
            return fh.read().strip() or "(empty)"
    except OSError:
        return "(no .hamilton/config)"


def models(cfg: dict) -> dict:
    """{step: model} from the `model.<step>` keys of `.hamilton/config`, as
    the engineer wrote them: which model a name means is the adapter's
    business, and so is the default for a step left unset."""
    return {key[len(MODEL_PREFIX):]: value.strip()
            for key, (value, _line) in cfg.items()
            if key.startswith(MODEL_PREFIX) and value.strip()}


def plan_answer(answer: str) -> tuple[dict, dict]:
    """The plan task's JSON, as ({qual: brief}, {qual: why it cannot be
    tested}). A plan answered in prose costs nothing: the writers get no
    brief, and nothing is flagged."""
    start, end = answer.find("{"), answer.rfind("}")
    if start < 0 or end < start:
        return {}, {}
    try:
        data = json.loads(answer[start:end + 1])
    except ValueError:
        return {}, {}
    if not isinstance(data, dict):
        return {}, {}

    def strings(value) -> dict:
        return ({str(k): str(v) for k, v in value.items()}
                if isinstance(value, dict) else {})
    return strings(data.get("briefs")), strings(data.get("infeasible"))


# --- the loop -----------------------------------------------------------------

class Run:
    """One `hamilton build`: the steps, and what the engineer sees of them."""

    def __init__(self, root: str, worker: P.Worker, judge: P.Judge,
                 console: Console, state: State) -> None:
        self.root = root
        self.worker = worker
        self.judge = judge
        self.console = console
        self.state = state
        self.rows = Rows()
        self.shown = _review.Shown(console, rows=self.rows)
        console.follow(self.rows)
        self.began = time.monotonic()
        self.spent: dict = {}           # SPENT key -> seconds
        self.checks = self.suites = 0
        self._open: tuple | None = None # (SPENT key, when it started)

    # -- where the time goes --

    def _close(self) -> None:
        if self._open:
            kind, since = self._open
            self.spent[kind] = (self.spent.get(kind, 0.0)
                                + max(0.0, time.monotonic() - since))
            self._open = None

    def waited(self, seconds: float) -> None:
        """The engineer's time at a question: booked apart, and taken out of
        the step it interrupted."""
        self.spent["wait"] = self.spent.get("wait", 0.0) + seconds
        if self._open:
            self._open = (self._open[0], self._open[1] + seconds)

    def times(self) -> str:
        """Where the run's time went, one line."""
        self._close()
        parts = []
        for kind, name in SPENT.items():
            if kind not in self.spent:          # never ran this run
                continue
            part = f"{name} {elapsed(self.spent[kind])}"
            if kind == "check" and self.checks:
                part += (f" ({self.checks}×, {self.suites} with the suite)"
                         if self.suites else f" ({self.checks}×)")
            parts.append(part)
        line = (f"Time {elapsed(time.monotonic() - self.began)} · "
                + " · ".join(parts))
        used = self.tokens()
        return f"{line}\n{used}" if used else line

    def tokens(self) -> str:
        """What the run's agents used, per kind of work, one line -- or
        nothing, when no agent ran."""
        used = {k: n for k, n in {**self.worker.tokens, **self.judge.tokens}.items()
                if n}
        if not used:
            return ""
        names = {**SPENT, "clarify": "clarifying"}
        return (f"Tokens {_tokens(sum(used.values()))} · "
                + " · ".join(f"{names.get(k, k)} {_tokens(n)}"
                             for k, n in sorted(used.items(), key=lambda kv: -kv[1])))

    # -- rendering --

    def step(self, key: str, detail: str = "") -> None:
        self._close()
        self._open = ("tests" if key == "retests" else key, time.monotonic())
        self.console.step(STEPS[key], detail)
        self.console.working_on(STEPS[key])

    def said(self, text: str) -> None:
        self.console.say(f"  {self.console.paint.dim(text)}")

    # -- steps --

    def check(self, suite: bool, recheck=()) -> list:
        """The gate: with the suite, or without it -- and then the tests of
        the criteria in `recheck` run on their own, a fix re-checked before
        the whole suite is worth running again."""
        detail = ("with the suite" if suite else
                  f"re-running {', '.join(recheck)}" if recheck else "")
        self.step("check", detail)
        started = time.monotonic()
        # The suite's output goes to a file, not the screen, so the indicator
        # keeps running through it: follow the file to watch, the build shows
        # the outcome, and a failure's output goes to the coding step.
        follow = lambda log: self.said(_verify.follow_hint(log))
        findings, _w, _n, _manual, _nr, _na = _verify.run(
            self.root, suite=suite, on_log=follow)
        if recheck and not suite:
            ran = _verify.run_criteria(self.root, list(recheck), on_log=follow)
            failed = _verify.still_failing(ran)
            if failed:
                findings.append(failed)
        self.checks += 1
        self.suites += suite
        took = f" ({elapsed(time.monotonic() - started)})"
        if not findings:
            self.said(("green" if suite else "nothing left to write or review") + took)
        else:
            counts: dict = {}
            for f in findings:
                counts[f["rule"]] = counts.get(f["rule"], 0) + 1
            self.said(" · ".join(f"{n} {rule}" for rule, n in sorted(counts.items()))
                      + took)
        return findings

    @property
    def command(self) -> str:
        """The project's full-suite command, named in a brief as the one not
        to run."""
        entry = _verify.read_config(self.root).get("test_command")
        return entry[0].strip() if entry else "(no test_command set)"

    async def task(self, key, label: str, prompt: str, step: str) -> str:
        """One AI task, as a row while it runs and a line when it is done --
        how long it took and the tokens it used. A task that does not come
        back is the step's failure, not the run's. `step` is the kind of
        work, which the worker may pick its model by."""
        self.rows.start(key, label)
        used: list = []
        try:
            try:
                answer = await self.worker.run(
                    prompt, on_action=lambda action: self.rows.doing(key, action),
                    step=step, on_tokens=used.append)
            except Exception as exc:
                raise Failed(label, exc) from exc
        finally:
            took = self.rows.stop(key)
        self.console.say(self.console.paint.dim(
            f"  ✓ {label} ({_cost(took, sum(used))})"))
        return answer

    async def plan(self, work: Work, reqs: dict, defined: dict) -> tuple[dict, dict]:
        self.step("plan", _count(len(work.to_write), "criterion", "criteria"))
        answer = await self.task("plan", "Plan the surfaces",
                                 plan_prompt(self.root, work, reqs, defined), "plan")
        return plan_answer(answer)

    async def tests(self, quals: list, reqs: dict, defined: dict, plans: dict,
                    paths: dict, rejected: dict) -> None:
        """One writer per criterion, a few at a time. Two criteria whose
        tests still share a file take turns on it -- from the first round,
        until each has moved into a file of its own."""
        again = any(rejected.get(q) for q in quals)
        self.step("retests" if again else "tests",
                  _count(len(quals), "criterion", "criteria"))
        slots = asyncio.Semaphore(WRITERS)
        files: dict = {}
        tagged = _verify.tagged_files(self.root, quals)

        async def write(qual: str) -> None:
            mine = sorted(tagged[qual] | {t["file"] for t in
                                          (rejected.get(qual) or {}).get("tests", ())})
            locks = [files.setdefault(f, asyncio.Lock()) for f in mine]
            async with slots, contextlib.AsyncExitStack() as held:
                for lock in locks:          # sorted: no two wait on each other
                    await held.enter_async_context(lock)
                await self.task(
                    qual, f"{qual}{' again' if rejected.get(qual) else ''}",
                    test_prompt(qual, reqs, defined, plans.get(qual, ""), paths,
                                rejected.get(qual), self.command), "tests")

        await asyncio.gather(*(write(q) for q in quals))

    async def review(self) -> list:
        """A first review for a criterion nobody has reviewed; for one that
        was, a re-review that settles what was said then."""
        again = bool(self.state.reviews)
        self.step("review", "settling the earlier comments" if again else "")
        started = time.monotonic()
        results = await _review.review(self.root, self.judge, watch=self.shown,
                                       memory=self.state.reviews)
        self.state.reviews = _review.remember(self.state.reviews, results)
        self.state.save(self.root)
        self.shown.report(results)
        if results:
            self.said(_cost(time.monotonic() - started,
                            sum(r.get("tokens", 0) for r in results)))
        return [r for r in results if r["ac"] not in self.state.skipped]

    async def code(self, work: Work, reqs: dict, defined: dict, quals: list) -> None:
        # Why it is coding: the criteria whose tests were just written, a
        # failing suite, or both.
        why = [", ".join(quals) if len(quals) <= 3
               else _count(len(quals), "criterion", "criteria")] if quals else []
        if work.suite:
            why.append("the suite is failing")
        self.step("code", " · ".join(why))
        await self.task("code", "Write the implementation",
                        code_prompt(work, reqs, defined, quals,
                                    test_files(self.root, quals), self.command),
                        "code")


def _tokens(n: int) -> str:
    """1234 -> 1.2k, 2345678 -> 2.3M."""
    for size, unit in ((1_000_000, "M"), (1_000, "k")):
        if n >= size:
            return f"{n / size:.1f}{unit}"
    return str(n)


def _cost(seconds: float, tokens: int) -> str:
    """What a piece of work took: "2m58s · 44.0k tokens"."""
    return elapsed(seconds) + (f" · {_tokens(tokens)} tokens" if tokens else "")


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


async def build(root: str, worker: P.Worker, judge: P.Judge, console: Console,
                state: State) -> int:
    """Round the loop until the gate is green, the engineer ends the run, or
    `PASSES` is spent. Returns the exit code."""
    run = Run(root, worker, judge, console, state)
    try:
        return await _loop(run, root, state, console)
    finally:
        # Where the time went, however the run ended.
        console.say()
        console.say(console.paint.dim(run.times()))
        # Whatever a review left open, for the engineer to unfold now that
        # nothing else wants the terminal. Off the loop: the list is a
        # terminal application with an event loop of its own.
        await asyncio.to_thread(run.shown.browse)


async def _loop(run: "Run", root: str, state: State, console: Console) -> int:
    started = time.monotonic()
    # The full suite runs only when nothing else is left: every step works on
    # its own tests, and this is the one run that checks them all together.
    # After a fix for a failing suite, the criteria it named are re-run on
    # their own first: while they fail, the suite has nothing to add.
    suite = False
    recheck: list = []
    passes = 0
    while True:
        try:
            findings = run.check(suite, recheck)
            recheck = []
        except UsageError as exc:
            console.error(f"build: {exc}")
            return 2
        if not findings:
            if suite:
                console.say()
                console.say(f"hamilton build: the gate is green "
                            f"({elapsed(time.monotonic() - started)}).")
                State.clear(root)
                return 0
            # The shape is clean; confirm with the suite. That check is no
            # pass of its own, so it happens however many were spent.
            suite = True
            continue
        if passes == PASSES:
            break
        passes += 1

        reqs, defined, paths = _model(root)
        work = route(findings, state.skipped)
        if work.spec:
            return _spec_defect(console, work, state, root)
        if not work.open:       # everything left is something they skipped
            return _only_skipped(console, work, state, root)

        try:
            await _pass(run, root, state, console, work, reqs, defined, paths)
        except Failed as exc:
            if await _task_failed(run, exc) is Answer.END:
                return 1
            suite = False           # try that step again, from the gate
            continue
        except _Stopped:
            return 1
        except _Again:
            suite = False           # a clarified criterion: back to the gate
            continue
        suite = False               # back to the gate; the suite at the end
        recheck = [q for q in failing(work) if q not in state.skipped]

    console.say()
    console.error("build: still not green after "
                  f"{PASSES} passes -- what is left is in `hamilton verify`.")
    state.stopped = "passes spent"
    state.save(root)
    return 1


class _Stopped(Exception):
    """The engineer ended the run at a stop."""


async def _pass(run: "Run", root: str, state: State, console: Console,
                work: Work, reqs: dict, defined: dict, paths: dict) -> None:
    """One time round: plan what is open, write and review until the tests
    pass or the engineer says otherwise, then implement."""
    # A criterion whose wording changed makes what was said of its tests moot.
    changed = [qual_of(f) for f in work.review if f.get("state") in CHANGED_STATES]
    state.reviews = _review.forget(state.reviews, changed)

    plans, infeasible = {}, {}
    if work.config or work.cover:
        plans, infeasible = await run.plan(work, reqs, defined)
    # The planner's JSON names criteria in its own words: only one the spec
    # declares can be clarified.
    infeasible = {q: why for q, why in infeasible.items() if _declared(reqs, q)}
    if infeasible:
        await _spec_gaps(run, infeasible, reqs)

    rejected: dict = {}
    quals = [q for q in work.to_write if q not in state.skipped]
    while True:
        writable = [q for q in quals if state.round_of(q) < ROUNDS
                    and q not in state.skipped]
        spent = [q for q in quals
                 if q not in writable and q not in state.skipped]
        if spent:
            # Their rounds are gone. Only the engineer can say what now:
            # another round, leave them, or stop.
            if await _exhausted(run, spent, rejected) is Answer.END:
                raise _Stopped()
            writable = [q for q in quals if state.round_of(q) < ROUNDS
                        and q not in state.skipped]
        if writable:
            await run.tests(writable, reqs, defined, plans, paths, rejected)
            for qual in writable:
                state.rewrote(qual)
            state.save(root)
        elif quals:
            break                           # nothing left that may be written
        results = await run.review()
        errors = [r for r in results if r["verdict"] == "error"]
        if errors:
            # The reviewer failed, or its answer did not parse: no review
            # happened, so no writer is sent to solve it and no round is spent.
            raise Failed(f"Review {', '.join(r['ac'] for r in errors)}",
                         RuntimeError(errors[0]["comments"][0]["text"]))
        unclear = {r["ac"]: r["question"] for r in results
                   if r["verdict"] == "unclear"}
        if unclear:
            await _spec_gaps(run, unclear, reqs)
        rejected = rejected_by_criterion(results, state.skipped)
        quals = list(rejected)
        if not quals:
            break

    # A changed criterion is implemented too: its wording may ask for more
    # than the code does, whether or not its tests had to change. So is one
    # whose tests the failing suite names.
    covered = [q for q in dict.fromkeys(work.to_write + changed + failing(work))
               if q not in state.skipped]
    if covered or work.suite or work.config:
        await run.code(work, reqs, defined, covered)


def _declared(reqs: dict, qual: str) -> bool:
    if not _QUAL_RE.fullmatch(qual):
        return False
    rid, acid = qual.split("/")
    return acid in reqs.get(rid, {}).get("acs", {})


def _model(root: str):
    reqs, _dupes, _malformed = _verify.extract(os.path.join(root, REQ_REL))
    defined = _verify.extract_methods(os.path.join(root, REQ_REL))
    paths = _verify.method_paths(_verify.read_config(root))
    return reqs, defined, paths


def _spec_defect(console: Console, work: Work, state: State, root: str) -> int:
    """A spec that does not hold together. Build phase cannot write `spec/`,
    so there is nothing to choose: report it and stop."""
    console.say()
    console.say(console.paint.heading("── The spec has to be fixed first ──"))
    for f in work.spec:
        console.say(f"  {console.paint.red('✗')} {f['message']}")
    console.say()
    console.say("Run `hamilton design` to repair it, then `hamilton build` again.")
    state.stopped = "spec defect"
    state.save(root)
    return 1


def _only_skipped(console: Console, work: Work, state: State, root: str) -> int:
    console.say()
    console.say(f"hamilton build: nothing left but the "
                f"{_count(len(state.skipped), 'criterion', 'criteria')} you "
                f"skipped ({', '.join(state.skipped)}). The gate stays red "
                f"until they are dealt with.")
    state.stopped = "skipped"
    state.save(root)
    return 1


async def _task_failed(run: "Run", failure: Failed) -> Answer:
    """A step's task did not come back -- the model, the network, a budget."""
    console, state, root = run.console, run.state, run.root
    answer = await _ask(run, Stop(
        headline="That step did not come back",
        lines=[f"  {console.paint.red('!')} {failure}"],
        retry="Try that step again",
    ))
    state.stopped = "task failed"
    state.save(root)
    return answer


class _Again(Exception):
    """The spec changed under the pass: start the next one from the gate."""


async def _spec_gaps(run: "Run", gaps: dict, reqs: dict) -> None:
    """Criteria the spec cannot settle, or no test by their method could
    satisfy -- {qual: the question}. One at a time the engineer clarifies it
    (into the spec), skips it, or ends the run. Raises `_Again` when the
    spec changed, `_Stopped` when they end it."""
    console, state = run.console, run.state
    changed = False
    for qual, question in gaps.items():
        rid, acid = qual.split("/")
        while True:
            answer = await _ask(run, Stop(
                headline="The criterion cannot settle what the test must prove",
                quals=[qual], clarify=True,
                lines=[f"  {console.paint.yellow('?')} "
                       f"{console.paint.bold(qual)}  {reqs[rid]['acs'][acid]['text']}",
                       *_review._field("Question", question, _review.MAX_WIDTH,
                                       console.paint),
                       "", "  Answer it here and it goes into the spec, or "
                           "sharpen the criterion in `hamilton design`."]))
            if answer is Answer.END:
                state.stopped = f"{qual} needs clarifying"
                state.save(run.root)
                raise _Stopped()
            if answer is Answer.SKIP:
                break
            if await _clarified(run, reqs, qual, question):
                changed = True
                break
    state.save(run.root)
    if changed:
        raise _Again()


WRITE_IT = "Write it to the spec"
CHANGE_IT = "Change it"


async def _clarified(run: "Run", reqs: dict, qual: str, question: str) -> bool:
    """Ask for the answer and have it drafted into an amendment -- the
    criterion reworded, its method changed, criteria added beside it -- then
    put it to the engineer as `hamilton design` would: write it, have it
    changed, or go back. False sends them back to the choice."""
    console = run.console
    said = await _human(run, console.ask_text, "Your answer")
    if not said:
        return False
    draft = change = ""
    while True:
        try:
            reply = await run.task(f"clarify {qual}", f"Draft the change to {qual}",
                                   _clarify.prompt(run.root, qual, question, said,
                                                   draft, change), "clarify")
            amendment = _clarify.parse(run.root, qual, reply)
        except (Failed, ValueError) as exc:
            console.say(f"  {console.paint.red('!')} {exc}")
            return False
        console.say()
        console.say(f"  {console.paint.bold(qual.split('/')[0])}  in {REQ_REL}")
        for text in _clarify.diff(amendment, console.paint):
            console.say(text)
        choice = await _human(
            run, console.choose,
            P.Question("Write this to the spec?",
                       (P.Choice(WRITE_IT, "then its tests are revised against it"),
                        P.Choice(CHANGE_IT, "say what, and it is drafted again"))),
            "Back")
        if choice == WRITE_IT:
            break
        if choice != CHANGE_IT:
            return False
        change = await _human(run, console.ask_text, "What should change?")
        if not change:
            return False
        draft = amendment.draft
    try:
        touched = _clarify.write(run.root, amendment)
    except ValueError as exc:       # the spec changed while they were answering
        console.say(f"  {console.paint.red('!')} {exc}")
        return False
    run.state.reviews = _review.forget(run.state.reviews, touched)
    for q in touched:
        run.state.rounds.pop(q, None)
    console.say(console.paint.dim(f"  {REQ_REL}: {', '.join(touched)} written"))
    return True


async def _exhausted(run: "Run", quals: list, rejected: dict) -> Answer:
    console, state, root = run.console, run.state, run.root
    open_ = [rejected[q] for q in quals if q in rejected]
    # A criterion with no review never had a test the gate recognises: the
    # writer ran, but left no `@covers` tag under the method's paths.
    none = [q for q in quals if q not in rejected]
    answer = await _ask(run, Stop(
        headline=(f"Still rejected after {ROUNDS} rewrites" if open_
                  else f"Still no test after {ROUNDS} attempts"),
        quals=quals,
        lines=[f"  {_review.line(r, console.paint)}" for r in open_]
              + [f"  {console.paint.red('✗')} {console.paint.bold(q)}  no test was "
                 f"written that the gate recognises -- a `@covers {q}` tag under "
                 f"its method's paths" for q in none],
        retry="Try another round",
    ))
    if answer is Answer.RETRY:
        for qual in quals:
            state.rounds[qual] = 0
    state.save(root)
    return answer


# --- the command --------------------------------------------------------------

def main() -> int:
    root = os.getcwd()
    console = Console()

    def refuse(message: str, rc: int = 1) -> int:
        console.error(f"build: {message}")
        return rc

    if not os.path.isdir(os.path.join(root, ".hamilton")):
        return refuse("no .hamilton/ here -- run from a Hamilton project root "
                      "(`hamilton init` first).", 2)
    inside = os.environ.get(SESSION_ENV)
    if inside:
        return refuse(f"already inside a Hamilton session ({SESSION_ENV}="
                      f"{inside!r}). Exit it and run `hamilton build` from a "
                      f"plain shell.")

    _phase.write(root, "build")
    os.environ[SESSION_ENV] = "build"
    console.banner(_status.render(root, "build"))

    state = State.load(root)
    if state.started and not _keep(console, state):
        State.clear(root)
        state = State()

    from hamilton_core.session.claude_sdk_adapter import (ClaudeSdkJudge,
                                                          ClaudeSdkWorker)
    try:
        chosen = models(_verify.read_config(root))
    except UsageError as exc:
        return refuse(str(exc), 2)
    worker = ClaudeSdkWorker(root, lambda target: _guard.decide(root, target),
                             chosen)
    judge = ClaudeSdkJudge(chosen.get("review"))
    console.start_working(STEPS["check"])
    try:
        return asyncio.run(build(root, worker, judge, console, state))
    except KeyboardInterrupt:
        console.error("interrupted -- run `hamilton build` again to carry on "
                      "from wherever `hamilton verify` now stands.")
        state.save(root)
        return 130
    except Exception as exc:        # a traceback tells the engineer nothing
        console.error(f"build: the run stopped on an error -- {exc}")
        state.stopped = str(exc)[:200]
        state.save(root)
        return 1
    finally:
        console.stop_working()


def _keep(console: Console, state: State) -> bool:
    """A previous run left something worth keeping: what the engineer chose to
    skip, and which tests have already had their rewrites."""
    console.say()
    console.say(console.paint.bold("A previous build run left a record"
                                   f"{' (' + state.stopped + ')' if state.stopped else ''}."))
    if state.skipped:
        console.note(f"  skipping: {', '.join(state.skipped)}")
    if state.rounds:
        console.note(f"  rewrites so far: "
                     + ", ".join(f"{q} {n}" for q, n in sorted(state.rounds.items())))
    if not console.interactive:
        return True             # nobody to ask: the default is to carry on
    return console.confirm("Carry on from there?")
