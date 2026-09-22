"""`hamilton build` -- the loop Hamilton drives.

Nothing here reaches a model. The worker is scripted: it records the prompt it
was given and, where the loop's progress depends on it, does what the real one
would have done (write a tagged test, say). What is pinned is Hamilton's own
decisions -- which step each `hamilton verify` rule reaches, when a test is
rewritten, when the run stops and what it asks.
"""

import asyncio
import io
import json
import os
import re

from conftest import copy_fixture, stamp

from hamilton_core import build as B
from hamilton_core import verify as C
from hamilton_core.session.console import Console

QUAL_RE = re.compile(r"R-\d{4}/AC\d+")


class FakeWorker:
    """Records every prompt. `act(prompt)` stands in for the work itself."""

    def __init__(self, act=None):
        self.prompts = []
        self._act = act

    async def run(self, prompt, on_action=None):
        self.prompts.append(prompt)
        if on_action:
            on_action("editing a file")
        return (self._act(prompt) if self._act else "") or ""

    def of(self, kind):
        """The prompts of one kind: 'plan', 'write_test' or 'implement'."""
        marks = {"plan": "into a contract a test can be written",
                 "write_test": "Write the tests for one acceptance criterion",
                 "implement": "Write the implementation"}
        return [p for p in self.prompts if marks[kind] in p]


class FakeJudge:
    """Reviews every test as `verdict(qual)` says: "pass", "reject" (with a
    reason) or "unclear" (with a question). A first review answers with lists,
    a re-review settles the points it was given -- resolved on a pass, left
    open on a reject -- the way the real reviewer is told to."""

    def __init__(self, verdict):
        self._verdict = verdict
        self.asked = []
        self.settles = []

    async def ask(self, prompt):
        self.asked.append(prompt)
        blocks = re.split(r"^## (?=R-\d{4}/AC\d+$)", prompt, flags=re.M)[1:]
        settling = "Comments to settle" in prompt
        if settling:
            self.settles.append(prompt)
        out = []
        for block in blocks:
            qual = block.splitlines()[0].strip()
            kind, text = self._verdict(qual)
            question = text if kind == "unclear" else ""
            if not settling:
                out.append({"ac": qual, "question": question,
                            "covered": ["asserts the outcome"] if kind == "pass" else [],
                            "comments": ([{"check": "clause-coverage", "text": text}]
                                         if kind == "reject" else [])})
                continue
            ok = kind == "pass"
            out.append({"ac": qual, "question": question,
                        "kept": {k: {"ok": True, "why": ""}
                                 for k in re.findall(r"^- (K\d+):", block, re.M)},
                        "resolved": {c: {"ok": ok, "why": "" if ok else text,
                                         "covers": "asserts it now"}
                                     for c in re.findall(r"^- (C\d+):", block, re.M)}})
        return json.dumps(out)


def passes(_qual):
    return ("pass", "")


def rejects(reason="asserts the status but not the body"):
    return lambda _qual: ("reject", reason)


def unclear(question="Is a skew of exactly 30s inside the window?"):
    return lambda _qual: ("unclear", question)


def writes_a_test(root, body="expect(true).toBe(true);"):
    """A worker that writes the tagged test it was asked for, as the real
    writer would, so the next `hamilton verify` sees it."""
    def act(prompt):
        if "Write the tests for one acceptance criterion" not in prompt:
            return ""
        qual = QUAL_RE.search(prompt).group(0)
        path = os.path.join(root, "tests", f"{qual.replace('/', '_')}.js")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"// @covers {qual}\nit('{qual}', () => {{ {body} }});\n")
        return f"wrote tests/{os.path.basename(path)}"
    return act


def project(tmp_path, fixture="clean"):
    d = copy_fixture(fixture, tmp_path)
    with open(f"{d}/.hamilton/phase", "w") as fh:
        fh.write("build")
    return d


def console(keys=""):
    """A console with nobody at it: a stop is reported, never asked."""
    out = io.StringIO()
    return Console(out=out, inp=io.StringIO(keys), color=False), out


class Answering(Console):
    """A console there *is* somebody at. It still has no terminal, so the
    question renders as the numbered fallback and the answers come from
    `keys` -- the cursor picker itself is `tests/test_console.py`'s business."""
    interactive = True


def answering(keys):
    out = io.StringIO()
    return Answering(out=out, inp=io.StringIO(keys), color=False), out


def run(root, worker, judge, console_, state=None):
    return asyncio.run(B.build(root, worker, judge, console_,
                               state or B.State()))


# --- the routing table -------------------------------------------------------

def finding(rule, req=None, ac=None, state=None):
    return {"rule": rule, "req": req, "ac": ac, "state": state,
            "file": "f", "line": 1, "message": f"f:1: {rule}: detail"}


def test_every_rule_reaches_exactly_one_step():
    work = B.route([finding("malformed"),
                    finding("no-test-command"),
                    finding("uncovered", "R-0001", "AC1"),
                    finding("wrong-method", "R-0001", "AC2"),
                    finding("orphan-tag"),
                    finding("tests-failed"),
                    finding("unreviewed", "R-0002", "AC1", C.AC_CHANGED),
                    finding("unreviewed", "R-0002", "AC2", C.NEVER_REVIEWED),
                    finding("unreviewed", "R-0002", "AC3", C.TEST_CHANGED),
                    finding("unreviewed", "R-0002", "AC4", C.BOTH_CHANGED)],
                   skipped=[])
    assert [f["rule"] for f in work.spec] == ["malformed"]
    assert [f["rule"] for f in work.config] == ["no-test-command"]
    assert [f["rule"] for f in work.suite] == ["tests-failed"]
    # a criterion that changed needs the test written again; the rest only
    # need judging
    assert work.to_write == ["R-0001/AC1", "R-0001/AC2", "R-0002/AC1",
                             "R-0002/AC4"]
    assert [f["ac"] for f in work.review] == ["AC2", "AC3"]


def test_a_missing_reference_goes_back_to_the_spec():
    work = B.route([finding("missing-reference", "R-0001", "AC1")], skipped=[])
    assert [f["rule"] for f in work.spec] == ["missing-reference"]
    assert not work.open


def test_a_task_is_told_which_spec_files_the_criterion_references():
    reqs = {"R-0001": {"title": "Price", "statement": "Priced per spec/price_model.md.",
                       "acs": {"AC1": {"text": "a -> b, see spec/table.md [unit]",
                                       "methods": ["unit"]}}}}
    text = B.spec_of(reqs, {"unit": {"description": "one module."}}, "R-0001/AC1")
    assert "References: spec/price_model.md, spec/table.md" in text


def test_a_skipped_criterion_is_set_aside_before_any_step_sees_it():
    work = B.route([finding("uncovered", "R-0001", "AC1"),
                    finding("uncovered", "R-0001", "AC2")],
                   skipped=["R-0001/AC1"])
    assert work.to_write == ["R-0001/AC2"]
    assert [f["ac"] for f in work.skipped] == ["AC1"]


def test_the_shape_of_the_spec_is_checked_without_the_suite(tmp_path):
    """A ten-minute suite is not worth re-running to learn that a tag is still
    unreviewed."""
    d = project(tmp_path, "tests-failed")
    findings, *_ = C.run(d, suite=False)
    assert [f["rule"] for f in findings if f["rule"] == "tests-failed"] == []
    findings, *_ = C.run(d)
    assert [f["rule"] for f in findings if f["rule"] == "tests-failed"] != []


# --- a run that reaches green ------------------------------------------------

def test_a_green_gate_needs_no_agent_at_all(tmp_path):
    d = project(tmp_path)
    stamp(d)
    worker, c = FakeWorker(), console()[0]
    assert run(d, worker, FakeJudge(passes), c) == 0
    assert worker.prompts == []


def test_an_uncovered_criterion_is_planned_written_reviewed_and_implemented(tmp_path):
    d = project(tmp_path, "uncovered")
    worker = FakeWorker(writes_a_test(d))
    judge = FakeJudge(passes)
    c, out = console()
    assert run(d, worker, judge, c) == 0

    # one plan, one writer per criterion, one implementation
    assert len(worker.of("plan")) == 1
    assert len(worker.of("write_test")) >= 1
    assert len(worker.of("implement")) == 1
    # the writer was told the criterion and never handed the implementation
    written = worker.of("write_test")[0]
    assert "Criterion:" in written and "Statement:" in written
    assert "this is the first attempt" in written
    # the review wrote the suffixes, so the gate went green
    assert C.run(d)[0] == []
    text = out.getvalue()
    for step in ("Checking the gate", "Planning", "Writing tests",
                 "Reviewing tests", "Coding"):
        assert f"▸ {step}" in text
    assert "the gate is green" in text


# --- rejects -----------------------------------------------------------------

def test_a_reject_goes_back_to_a_writer_with_the_reviewers_reasons(tmp_path):
    d = project(tmp_path, "uncovered")
    verdicts = [rejects("the body is never asserted"), passes]
    judge = FakeJudge(lambda q: verdicts[min(len(judge.asked) - 1, 1)](q))
    worker = FakeWorker(writes_a_test(d))
    c, out = console()
    assert run(d, worker, judge, c) == 0
    written = worker.of("write_test")
    assert len(written) == 2
    assert "the body is never asserted" in written[1]
    assert "▸ Revising tests" in out.getvalue()


def test_a_test_still_rejected_after_its_rounds_asks_what_to_do(tmp_path):
    """No test is rewritten for ever: after its rounds the engineer says
    whether to spend more of them, leave that criterion, or stop."""
    d = project(tmp_path, "uncovered")
    worker = FakeWorker(writes_a_test(d))
    state = B.State()
    # 1) another round  2) skip it  3) end the run
    c, out = answering("2\n" * 4)
    assert run(d, worker, FakeJudge(rejects()), c, state) == 1

    tries = [QUAL_RE.search(p).group(0) for p in worker.of("write_test")]
    assert tries, "the writer ran at all"
    for qual in set(tries):
        assert tries.count(qual) <= B.ROUNDS, qual
    assert "R-0001/AC2" in state.skipped
    text = out.getvalue()
    assert f"Still rejected after {B.ROUNDS} rewrites" in text
    assert "the gate stays red" in text.lower()


def test_an_unclear_verdict_stops_without_another_rewrite(tmp_path):
    """The criterion cannot settle what the test must prove: no rewrite can
    fix that, and build phase cannot touch the spec."""
    d = project(tmp_path, "uncovered")
    worker = FakeWorker(writes_a_test(d))
    c, out = console()              # no terminal: reported, not asked
    assert run(d, worker, FakeJudge(unclear()), c, B.State()) == 1
    assert len(worker.of("write_test")) == 1
    text = out.getvalue()
    assert "The criterion cannot settle what the test must prove" in text
    assert "Is a skew of exactly 30s inside the window?" in text
    assert "hamilton design" in text


def test_without_a_terminal_a_stop_is_reported_and_nothing_is_asked(tmp_path):
    d = project(tmp_path, "uncovered")
    c, out = console("1\n")         # an answer nobody will read
    assert run(d, FakeWorker(writes_a_test(d)), FakeJudge(rejects()), c) == 1
    assert "What now?" not in out.getvalue()


# --- a spec that does not hold together --------------------------------------

def test_a_spec_defect_stops_the_run_without_touching_anything(tmp_path):
    d = project(tmp_path, "dangling-ref")
    worker = FakeWorker()
    c, out = console()
    assert run(d, worker, FakeJudge(passes), c) == 1
    assert worker.prompts == []
    assert "The spec has to be fixed first" in out.getvalue()
    assert "hamilton design" in out.getvalue()


# --- what the run remembers --------------------------------------------------

def test_the_state_keeps_what_a_re_run_could_not_work_out(tmp_path):
    d = project(tmp_path)
    state = B.State()
    state.skip("R-0001/AC1")
    state.rewrote("R-0001/AC2")
    state.save(d)
    back = B.State.load(d)
    assert back.skipped == ["R-0001/AC1"] and back.round_of("R-0001/AC2") == 1
    assert back.started is True
    B.State.clear(d)
    assert B.State.load(d).started is False


def test_a_state_file_from_a_later_version_is_not_a_reason_to_refuse(tmp_path):
    d = project(tmp_path)
    with open(os.path.join(d, B.STATE_REL), "w") as fh:
        fh.write('{"skipped": ["R-0001/AC1"], "from_a_later_version": 7}')
    assert B.State.load(d).skipped == ["R-0001/AC1"]
    with open(os.path.join(d, B.STATE_REL), "w") as fh:
        fh.write("{not json")
    assert B.State.load(d).skipped == []


def test_a_finished_run_leaves_no_state_behind(tmp_path):
    d = project(tmp_path)
    stamp(d)
    B.State(skipped=["R-0001/AC1"]).save(d)
    run(d, FakeWorker(), FakeJudge(passes), console()[0])
    assert not os.path.exists(os.path.join(d, B.STATE_REL))


def test_another_round_can_be_spent_on_a_rejected_test(tmp_path):
    """The first row is another round: the engineer gets to decide that the
    writer deserves one more go, and the budget starts again."""
    d = project(tmp_path, "uncovered")
    worker = FakeWorker(writes_a_test(d))
    state = B.State()
    c, _out = answering("1\n" + "2\n" * 4)      # one more round, then skip
    assert run(d, worker, FakeJudge(rejects()), c, state) == 1
    tries = [QUAL_RE.search(p).group(0) for p in worker.of("write_test")]
    assert tries.count("R-0001/AC2") > B.ROUNDS


def test_what_did_not_pass_is_left_for_the_engineer_to_unfold(tmp_path):
    d = project(tmp_path, "uncovered")
    c, out = console()
    run(d, FakeWorker(writes_a_test(d)), FakeJudge(rejects("too narrow")), c)
    text = out.getvalue()
    assert "need attention:" in text
    assert "• too narrow" in text          # no terminal: printed unfolded


def test_a_task_that_does_not_come_back_is_the_steps_failure_not_the_runs(tmp_path):
    """A model error ends up as a choice, not a traceback."""
    class Broken(FakeWorker):
        async def run(self, prompt, on_action=None):
            self.prompts.append(prompt)
            raise RuntimeError("the agent session failed: out of budget")

    d = project(tmp_path, "uncovered")
    c, out = console()              # nobody to ask: reported, then ended
    assert run(d, Broken(), FakeJudge(passes), c) == 1
    text = out.getvalue()
    assert "That step did not come back" in text
    assert "out of budget" in text


def test_a_failed_step_can_be_tried_again(tmp_path):
    once = {"failed": False}

    class Flaky(FakeWorker):
        async def run(self, prompt, on_action=None):
            if not once["failed"] and "into a contract a test can be written" in prompt:
                once["failed"] = True
                raise RuntimeError("temporary failure")
            return await super().run(prompt, on_action)

    d = project(tmp_path, "uncovered")
    c, _out = answering("1\n")      # the first row: try that step again
    assert run(d, Flaky(writes_a_test(d)), FakeJudge(passes), c) == 0
    assert once["failed"] is True


def test_a_test_that_only_needed_judging_costs_no_implementation(tmp_path):
    """A tag goes unreviewed when its test is edited. Reviewing it again is
    the whole job: there is nothing to plan and nothing to build."""
    d = project(tmp_path)
    stamp(d)
    body = open(f"{d}/tests/covers.js").read()
    open(f"{d}/tests/covers.js", "w").write(body + "\n// a comment, which is an edit\n")
    worker = FakeWorker()
    assert run(d, worker, FakeJudge(passes), console()[0]) == 0
    assert worker.of("implement") == [] and worker.of("plan") == []


def test_an_error_nothing_expected_is_reported_not_traced(tmp_path, monkeypatch):
    """Whatever goes wrong, the engineer gets a sentence, not a traceback."""
    monkeypatch.chdir(project(tmp_path))
    monkeypatch.setenv("HAMILTON_SESSION", "")
    monkeypatch.delenv("HAMILTON_SESSION")
    monkeypatch.setattr(B, "build", broken_build)
    assert B.main() == 1


async def broken_build(*_args, **_kw):
    raise RuntimeError("the reviewer session failed: out of budget")


def several_rejected_tests(root):
    """R-0001/AC1 with two tagged tests in one file, both unreviewed -- what
    klimasofort's `deployConfig.test.js` looked like."""
    stamp(root)
    with open(f"{root}/tests/pair.js", "w") as fh:
        fh.write("// @covers R-0001/AC1\nit('a', () => {});\n\n"
                 "// @covers R-0001/AC1\nit('b', () => {});\n")


def test_a_criterion_with_several_rejected_tests_gets_one_writer(tmp_path):
    """Each rejected tag used to add its criterion again: several writers
    rewrote the same file at once, and the rounds ran out in one round. Now
    the criterion is reviewed once, and revised by one writer that sees all
    of its tests."""
    d = project(tmp_path)
    several_rejected_tests(d)
    verdicts = iter([rejects("the body is never asserted")])
    judge = FakeJudge(lambda q: next(verdicts, passes)(q))
    worker = FakeWorker()
    run(d, worker, judge, console()[0])

    [rewrite] = worker.of("write_test")
    assert "tests/pair.js:1" in rewrite and "tests/pair.js:4" in rewrite
    assert "in a file of their own" in rewrite.lower()


def test_a_rewrite_is_labelled_as_one(tmp_path):
    d = project(tmp_path)
    several_rejected_tests(d)
    verdicts = iter([rejects()] * 2)
    judge = FakeJudge(lambda q: next(verdicts, passes)(q))
    c, out = console()
    run(d, FakeWorker(), judge, c)
    assert "▸ Revising tests — 1 criterion" in out.getvalue()
    assert "▸ Writing tests" not in out.getvalue()


def test_the_stop_lists_what_is_still_open_once_each(tmp_path):
    d = project(tmp_path)
    several_rejected_tests(d)
    c, out = console()
    run(d, FakeWorker(), FakeJudge(rejects()), c)
    text = out.getvalue()
    stop = text[text.index("Still rejected"):text.index("need attention")]
    assert stop.count("R-0001/AC1") == 1          # one line for the criterion
    assert "+2 more" in stop                      # ... standing for its three tests
    assert "✓" not in stop


def test_criteria_whose_tests_share_a_file_take_turns_on_it(tmp_path):
    d = project(tmp_path)
    stamp(d)
    with open(f"{d}/tests/shared.js", "w") as fh:
        fh.write("// @covers R-0001/AC1\nit('a', () => {});\n\n"
                 "// @covers R-0001/AC2\nit('b', () => {});\n")
    busy, overlaps = set(), []

    class Watching(FakeWorker):
        async def run(self, prompt, on_action=None):
            if "shared.js" in prompt:
                if busy:
                    overlaps.append(prompt)
                busy.add(prompt)
                await asyncio.sleep(0.02)
                busy.discard(prompt)
            return await super().run(prompt, on_action)

    verdicts = iter([rejects()] * 2)
    run(d, Watching(), FakeJudge(lambda q: next(verdicts, passes)(q)), console()[0])
    assert overlaps == []


# --- a review that is settled, round over round -------------------------------

def test_a_revise_gets_the_comments_to_solve_and_the_points_to_keep(tmp_path):
    d = project(tmp_path)
    several_rejected_tests(d)
    judge = FakeJudge(rejects("the body is never asserted"))
    worker = FakeWorker()
    run(d, worker, judge, console()[0])
    revise = worker.of("write_test")[0]
    assert "Open comments -- solve each:" in revise
    assert "- the body is never asserted" in revise
    # the next review of a revised test settles its list; it is no first review
    assert judge.settles and "Comments to settle" in judge.settles[0]


def test_the_list_only_shrinks_until_the_test_passes(tmp_path):
    """Two comments; each re-review resolves one. Nothing new ever appears, so
    the run converges instead of chasing a moving target."""
    d = project(tmp_path)
    several_rejected_tests(d)
    first = {"check": "scope", "text": "only one token"}
    second = {"check": "can-fail", "text": "loose assertion"}
    rounds = []

    class Converging:
        async def ask(self, prompt):
            quals = re.findall(r"^## (R-\d{4}/AC\d+)$", prompt, re.M)
            if "Comments to settle" not in prompt:
                return json.dumps([{"ac": q, "covered": ["a 401"], "question": "",
                                    "comments": [first, second]} for q in quals])
            rounds.append(prompt)
            ids = re.findall(r"^- (C\d+):", prompt, re.M)
            return json.dumps([{"ac": q, "question": "",
                                "kept": {k: {"ok": True, "why": ""} for k in
                                         re.findall(r"^- (K\d+):", prompt, re.M)},
                                "resolved": {c: {"ok": i == 0, "why": "not yet",
                                                 "covers": f"fixed {c}"}
                                             for i, c in enumerate(ids)}}
                               for q in quals])

    state = B.State()
    assert run(d, FakeWorker(), Converging(), console()[0], state) == 0
    # the criterion's tests are re-reviewed together: two open comments, then
    # one, then a pass -- the list never grows
    open_counts = [len(re.findall(r"^- C\d+:", r, re.M)) for r in rounds]
    assert open_counts == [2, 1]


# --- what cannot be tested ----------------------------------------------------

def test_the_planner_screens_out_an_untestable_criterion_before_any_writer(tmp_path):
    d = project(tmp_path, "uncovered")

    def plans(prompt):
        if "into a contract a test can be written" in prompt:
            return json.dumps({"briefs": {}, "infeasible": {
                "R-0001/AC2": "Which method can observe the clock skew?"}})
        return ""

    worker = FakeWorker(plans)
    c, out = console()
    assert run(d, worker, FakeJudge(passes), c) == 1
    assert worker.of("write_test") == []
    assert "Which method can observe the clock skew?" in out.getvalue()


# --- clarifying the spec from the run -----------------------------------------

REWORDED = "- AC2: token at most 30s past exp (inclusive) -> accepted [http]"


def clarifying(root):
    """A worker that writes tests and, asked to clarify, drafts REWORDED."""
    write = writes_a_test(root)

    def act(prompt):
        if "## The engineer's answer" in prompt:
            return REWORDED
        return write(prompt)
    return act


def unclear_once():
    asked = []

    def verdict(_qual):
        asked.append(1)
        return unclear("Is 30s itself inside the window?") if len(asked) == 1 else passes
    return lambda q: verdict(q)(q)


def spec(root):
    return open(os.path.join(root, "spec", "requirements.md")).read().splitlines()


def test_a_clarification_goes_into_the_spec_and_the_run_carries_on(tmp_path):
    d = project(tmp_path, "uncovered")
    before = spec(d)
    # 1) Clarify it, then the answer, then 1) Write it to the spec
    c, out = answering("1\nyes, inclusive\n1\n")
    assert run(d, FakeWorker(clarifying(d)), FakeJudge(unclear_once()), c) == 0
    after = spec(d)
    changed = [(a, b) for a, b in zip(before, after) if a != b]
    assert len(after) == len(before) and len(changed) == 1
    assert changed[0][1] == REWORDED               # the id and the marker kept
    text = out.getvalue()
    assert "  - AC2: token inside the 30s clock-skew window" in text
    assert f"  + {REWORDED.removeprefix('- ')}" in text
    assert "the gate is green" in text


def test_declining_the_draft_changes_nothing_and_asks_again(tmp_path):
    d = project(tmp_path, "uncovered")
    before = spec(d)
    # clarify, answer, 3) Back from the draft -- then skip it
    c, _out = answering("1\nyes, inclusive\n3\n2\n")
    assert run(d, FakeWorker(clarifying(d)), FakeJudge(unclear()), c) == 1
    assert spec(d) == before


def test_an_amendment_may_change_the_method_and_add_criteria(tmp_path):
    """The klimasofort case: the criterion is really a manual check, and the
    answer asks for two criteria the tests can prove instead."""
    from hamilton_core import clarify
    d = project(tmp_path)
    draft = ("- AC2: token inside the skew window -> accepted by the host [manual]\n"
             "- NEW: token 30s past exp -> accepted [http]\n"
             "- NEW: token 31s past exp -> 401 [http]\n")
    a = clarify.parse(d, "R-0001/AC2", draft)
    assert a.new == "- AC2: token inside the skew window -> accepted by the host [manual]"
    assert a.added == ["- AC3: token 30s past exp -> accepted [http]",   # numbered by Hamilton
                       "- AC4: token 31s past exp -> 401 [http]"]
    before = spec(d)
    assert clarify.write(d, a) == ["R-0001/AC2", "R-0001/AC3", "R-0001/AC4"]
    after = spec(d)
    assert len(after) == len(before) + 2
    i = after.index(a.new)
    assert after[i + 1:i + 3] == a.added
    assert [ln for ln in after if ln not in (a.new, *a.added)] == \
        [ln for ln in before if ln != a.old]                    # nothing else moved
    C.run(d, suite=False)                                       # still a valid spec


def test_an_amendment_stays_inside_what_build_may_decide(tmp_path):
    import pytest
    from hamilton_core import clarify
    d = project(tmp_path)
    with pytest.raises(ValueError, match="hamilton design"):     # a method not defined
        clarify.parse(d, "R-0001/AC2", "- AC2: the host issues a certificate [hosting]")
    with pytest.raises(ValueError, match="hamilton design"):
        clarify.parse(d, "R-0001/AC2", "NEW METHOD NEEDED: inspect the deployed host")
    with pytest.raises(ValueError, match="other than the one"):  # another criterion
        clarify.parse(d, "R-0001/AC2", "- AC2: x -> y [http]\n- AC1: z -> w [http]")
    with pytest.raises(ValueError, match="does not start"):
        clarify.parse(d, "R-0001/AC2", "- NEW: x -> y [http]")


def test_change_it_drafts_again_with_what_the_engineer_said(tmp_path):
    d = project(tmp_path, "uncovered")
    drafts = []

    def act(prompt):
        if "## The engineer's answer" in prompt:
            drafts.append(prompt)
            return REWORDED if len(drafts) == 1 else REWORDED.replace("30s", "45s")
        return writes_a_test(d)(prompt)

    # clarify, answer, 2) Change it, say what, then 1) write the second draft
    c, _out = answering("1\nyes, inclusive\n2\nmake it 45 seconds\n1\n")
    assert run(d, FakeWorker(act), FakeJudge(unclear_once()), c) == 0
    assert len(drafts) == 2
    assert "make it 45 seconds" in drafts[1] and REWORDED in drafts[1]
    assert REWORDED.replace("30s", "45s") in spec(d)


# --- the memory of a review, across runs --------------------------------------

def test_what_the_reviewer_said_survives_a_re_run(tmp_path):
    d = project(tmp_path)
    several_rejected_tests(d)
    run(d, FakeWorker(), FakeJudge(rejects("too loose")), console()[0])
    kept = B.State.load(d)
    assert "R-0001/AC1" in kept.reviews

    # the next run starts where this one stopped: its first review is a settle
    kept.rounds = {}
    judge = FakeJudge(passes)
    assert run(d, FakeWorker(), judge, console()[0], kept) == 0
    assert "Comments to settle" in judge.asked[0]


def test_without_a_terminal_a_re_run_carries_on_from_the_record(tmp_path):
    """EOF is no answer; the default -- keep what the last run learnt -- holds."""
    state = B.State(skipped=["R-0001/AC1"])
    c, out = console()
    assert B._keep(c, state) is True
    assert "Carry on from there?" not in out.getvalue()


def test_a_record_from_the_per_test_layout_is_dropped_not_carried(tmp_path):
    d = project(tmp_path)
    with open(os.path.join(d, B.STATE_REL), "w") as fh:
        fh.write(json.dumps({"reviews": {
            "tests/a.js::R-0001/AC1::1": {"covered": [], "comments": []},
            "R-0001/AC2": {"covered": ["x"], "comments": []}}}))
    assert list(B.State.load(d).reviews) == ["R-0001/AC2"]


def test_a_writer_that_leaves_no_test_is_named_for_what_it_is(tmp_path):
    """Nothing was rejected -- nothing was written. The stop says so."""
    d = project(tmp_path, "uncovered")
    c, out = console()
    assert run(d, FakeWorker(), FakeJudge(passes), c) == 1
    text = out.getvalue()
    assert f"Still no test after {B.ROUNDS} attempts" in text
    assert "no test was written that the gate recognises" in text


def test_a_failing_suite_s_output_goes_to_the_coder_not_the_screen(tmp_path):
    d = project(tmp_path)
    stamp(d)
    cfg = open(f"{d}/.hamilton/config").read().replace(
        "test_command=true", "test_command=echo 'AssertionError: expected 401, got 200' && exit 1")
    open(f"{d}/.hamilton/config", "w").write(cfg)
    worker = FakeWorker()
    c, out = console()
    run(d, worker, FakeJudge(passes), c)
    [coding, *_] = worker.of("implement")
    assert "AssertionError: expected 401, got 200" in coding
    assert "AssertionError" not in out.getvalue()



def test_the_coding_step_says_why_it_is_coding(tmp_path):
    """Not "0 criteria" when it runs to fix a failing suite."""
    d = project(tmp_path)
    stamp(d)
    cfg = open(f"{d}/.hamilton/config").read().replace("test_command=true",
                                                       "test_command=false")
    open(f"{d}/.hamilton/config", "w").write(cfg)
    c, out = console()
    run(d, FakeWorker(), FakeJudge(passes), c)
    assert "▸ Coding — the suite is failing" in out.getvalue()
    assert "0 criteria" not in out.getvalue()


# --- the suite runs once, and the time is shown --------------------------------

def counting_suite(monkeypatch):
    """Count full-suite runs: `check.run_tests` is the one place they happen."""
    runs = []
    real = C.run_tests

    def counted(root, cfg, echo=False, log=None):
        runs.append(root)
        return real(root, cfg, echo, log)
    monkeypatch.setattr(C, "run_tests", counted)
    return runs


def test_the_full_suite_runs_once_at_the_end(tmp_path, monkeypatch):
    runs = counting_suite(monkeypatch)
    d = project(tmp_path, "uncovered")
    c, out = console()
    assert run(d, FakeWorker(writes_a_test(d)), FakeJudge(passes), c) == 0
    assert len(runs) == 1
    steps = [ln for ln in out.getvalue().splitlines() if ln.startswith("▸ Checking")]
    assert steps[-1] == "▸ Checking the gate — with the suite"
    assert all("with the suite" not in s for s in steps[:-1])


def test_a_suite_that_fails_at_the_end_goes_to_coding_and_runs_again(tmp_path, monkeypatch):
    runs = counting_suite(monkeypatch)
    d = project(tmp_path)
    stamp(d)
    cfg = open(f"{d}/.hamilton/config").read()
    open(f"{d}/.hamilton/config", "w").write(
        cfg.replace("test_command=true", "test_command=test -f fixed"))

    def fixes(prompt):
        if "Write the implementation" in prompt:
            open(f"{d}/fixed", "w").write("")
        return ""
    worker = FakeWorker(fixes)
    assert run(d, worker, FakeJudge(passes), console()[0]) == 0
    assert len(runs) == 2 and len(worker.of("implement")) == 1


def test_a_writer_runs_only_its_own_tests(tmp_path):
    d = project(tmp_path, "uncovered")
    worker = FakeWorker(writes_a_test(d))
    run(d, worker, FakeJudge(passes), console()[0])
    brief = worker.of("write_test")[0]
    assert "Run it -- and only it." in brief
    assert "Never run the full suite** (`true`)" in brief     # the fixture's test_command


def test_the_coder_is_given_the_tests_to_run(tmp_path):
    d = project(tmp_path, "uncovered")
    worker = FakeWorker(writes_a_test(d))
    run(d, worker, FakeJudge(passes), console()[0])
    [coding] = worker.of("implement")
    assert "- tests/R-0001_AC2.js" in coding
    assert "Do **not** run the\nfull suite (`true`)" in coding


def test_a_run_ends_with_where_its_time_went(tmp_path):
    d = project(tmp_path, "uncovered")
    c, out = console()
    run(d, FakeWorker(writes_a_test(d)), FakeJudge(passes), c)
    [times] = [ln for ln in out.getvalue().splitlines() if ln.startswith("Time ")]
    for kind in ("checking", "planning", "writing tests", "reviewing", "coding"):
        assert kind in times, kind
    assert "with the suite)" in times


def test_waiting_for_the_engineer_is_booked_apart():
    """A question the engineer takes a while over is theirs, not the step's."""
    r = B.Run("/tmp", FakeWorker(), FakeJudge(passes), console()[0], B.State())
    r.step("review")
    r.waited(90)
    line = r.times()
    assert "waiting for you 1m30s" in line
    assert "reviewing 0s" in line



def test_the_suite_can_be_followed_in_its_log_not_on_the_screen(tmp_path):
    """Its output is in a file named before it starts -- `tail -f` it to
    watch -- and none of it reaches the scrollback."""
    d = project(tmp_path)
    stamp(d)
    cfg = open(f"{d}/.hamilton/config").read().replace(
        "test_command=true",
        "test_command=echo '== PHP unit tests ==' && echo 'expected 1, got 2' && exit 1")
    open(f"{d}/.hamilton/config", "w").write(cfg)
    c, out = console()
    r = B.Run(d, FakeWorker(), FakeJudge(passes), c, B.State())
    r.check(suite=True)
    text = out.getvalue()
    log = text.split("follow it: tail -f ")[1].split()[0]
    assert "expected 1, got 2" in open(log).read()
    assert "expected 1, got 2" not in text and "PHP unit tests" not in text


# --- the loop's edges -----------------------------------------------------------

def test_the_confirming_suite_run_is_no_pass_of_its_own(tmp_path, monkeypatch):
    """A gate that first comes out clean on the last pass still gets its suite
    run -- and goes green, not "still not green"."""
    monkeypatch.setattr(B, "PASSES", 1)
    d = project(tmp_path, "uncovered")
    c, out = console()
    assert run(d, FakeWorker(writes_a_test(d)), FakeJudge(passes), c) == 0
    assert "the gate is green" in out.getvalue()


class Garbled:
    """A reviewer whose first answer does not parse; after that, `then`."""

    def __init__(self, then):
        self.then = FakeJudge(then)
        self.asked = []

    async def ask(self, prompt):
        self.asked.append(prompt)
        if len(self.asked) == 1:
            return "I could not decide."
        return await self.then.ask(prompt)


def test_a_reviewer_error_is_a_failed_step_not_a_reject(tmp_path):
    """No writer is sent to "solve" the error, and no round is spent on it."""
    d = project(tmp_path, "uncovered")
    worker, state = FakeWorker(writes_a_test(d)), B.State()
    c, out = console()              # nobody to ask: reported, then ended
    assert run(d, worker, Garbled(passes), c, state) == 1
    assert "That step did not come back" in out.getvalue()
    assert "Review R-0001/AC2" in out.getvalue()
    assert len(worker.of("write_test")) == 1
    assert state.round_of("R-0001/AC2") == 1


def test_a_failed_review_can_be_tried_again(tmp_path):
    d = project(tmp_path, "uncovered")
    worker = FakeWorker(writes_a_test(d))
    c, _out = answering("1\n")      # the first row: try that step again
    assert run(d, worker, Garbled(passes), c) == 0
    assert len(worker.of("write_test")) == 1


def test_the_planner_naming_no_criterion_of_the_spec_is_ignored(tmp_path):
    d = project(tmp_path, "uncovered")

    def plans(prompt):
        if "into a contract a test can be written" in prompt:
            return json.dumps({"briefs": {}, "infeasible": {
                "R-0003": "a requirement, not a criterion",
                "R-0009/AC1": "no such requirement",
                "R-0001/AC9": "no such criterion"}})
        return writes_a_test(d)(prompt)

    c, out = console()
    assert run(d, FakeWorker(plans), FakeJudge(passes), c) == 0
    assert "cannot settle" not in out.getvalue()


def test_first_round_writers_whose_tests_share_a_file_take_turns_on_it(tmp_path):
    """Before any review there is no list of a criterion's files but the
    tags: two writers still must not edit the same file at once."""
    d = project(tmp_path)
    stamp(d)
    with open(f"{d}/tests/shared.js", "w") as fh:
        fh.write("// @covers R-0001/AC1\nit('a', () => {});\n\n"
                 "// @covers R-0001/AC2\nit('b', () => {});\n")
    busy, overlaps = [], []

    class Watching(FakeWorker):
        async def run(self, prompt, on_action=None):
            if busy:
                overlaps.append(prompt)
            busy.append(prompt)
            await asyncio.sleep(0.02)
            busy.remove(prompt)
            return await super().run(prompt, on_action)

    r = B.Run(d, Watching(), FakeJudge(passes), console()[0], B.State())
    reqs, defined, paths = B._model(d)
    asyncio.run(r.tests(["R-0001/AC1", "R-0001/AC2"], reqs, defined, {}, paths, {}))
    assert overlaps == []


def test_a_spec_that_changed_while_the_engineer_answered_is_not_overwritten(tmp_path):
    """The amendment was drafted against a line that is no longer there: it
    is reported, and the engineer is back at the choice."""
    d = project(tmp_path, "uncovered")
    req = os.path.join(d, "spec", "requirements.md")

    class EditedMeanwhile(Answering):
        def choose(self, q, finish):
            if q.prompt == "Write this to the spec?":
                # someone edits the criterion while the draft is on screen
                text = open(req).read().replace("clock-skew window", "skew window")
                open(req, "w").write(text)
            return super().choose(q, finish)

    out = io.StringIO()
    # clarify, answer, 1) write it -- refused -- then 2) skip it
    c = EditedMeanwhile(out=out, inp=io.StringIO("1\nyes, inclusive\n1\n2\n"),
                        color=False)
    assert run(d, FakeWorker(clarifying(d)), FakeJudge(unclear()), c) == 1
    assert "changed while the run was waiting" in out.getvalue()
    assert REWORDED not in spec(d)
