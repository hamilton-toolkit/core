"""The test reviewer (D-020) -- `hamilton build`'s review step, driven by a
fake `Judge`.

Nothing here reaches a model: the judge is scripted, and what is pinned is
what Hamilton does around it -- which tags it sends, what the prompt holds
(and does not), how verdicts land in the files, and how they are reported.
"""

import asyncio
import json
import os
import re

import pytest

from conftest import copy_fixture, run_verify, stamp

from hamilton_core import review as R
from hamilton_core.session.console import Paint

WEB_TEST = """import { get } from './support.js';

// @covers R-0001/AC1
it('rejects an expired token', async () => {
  expect((await get('/me', EXPIRED)).status).toBe(401);
});
// @covers R-0001/AC2
it('accepts a token inside the skew window', async () => {
  expect((await get('/me', SKEWED)).status).toBe(200);
});
"""


class FakeJudge:
    """Answers every prompt with ``reply(prompt)`` and keeps the prompts."""

    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    async def ask(self, prompt, on_tokens=None):
        self.prompts.append(prompt)
        return self.reply(prompt)


def verdicts(verdict, reasons=(), question=""):
    """A first review that comes out as ``verdict`` for every AC the prompt
    names: a pass covers something, a reject has ``reasons`` as its comments,
    an unclear asks ``question``."""
    def reply(prompt):
        acs = dict.fromkeys(re.findall(r"^## (R-\d{4}/AC\d+)$", prompt, re.M))
        return json.dumps([{"ac": ac,
                            "covered": ["asserts the outcome"] if verdict == "pass" else [],
                            "comments": [{"check": "clause-coverage", "text": r}
                                         for r in reasons],
                            "question": question} for ac in acs])
    return reply


def project(tmp_path):
    """The clean fixture with a two-test file, AC1 reviewed and AC2 not."""
    d = copy_fixture("clean", tmp_path)
    open(f"{d}/.hamilton/phase", "w").write("build")
    open(f"{d}/tests/covers.js", "w").write(WEB_TEST)
    stamp(d)
    body = open(f"{d}/tests/covers.js").read()
    ac2 = next(ln for ln in body.splitlines() if "R-0001/AC2" in ln)
    open(f"{d}/tests/covers.js", "w").write(body.replace(ac2, "// @covers R-0001/AC2"))
    return d


def reviewed(d, judge, **kw):
    return asyncio.run(R.review(d, judge, **kw))


def read(d, rel="tests/covers.js"):
    return open(os.path.join(d, rel)).read()


PAINT = Paint(False)


def test_a_pass_writes_exactly_that_tags_suffix(tmp_path):
    d = project(tmp_path)
    before = read(d)
    judge = FakeJudge(verdicts("pass"))
    [r] = reviewed(d, judge)
    after = read(d)
    changed = [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
    assert len(changed) == 1 and len(judge.prompts) == 1
    old, new = changed[0]
    assert old == "// @covers R-0001/AC2"
    assert new.startswith("// @covers R-0001/AC2 #") and len(new) == len(old) + 15
    assert run_verify(d).returncode == 0
    assert R.line(r, PAINT) == "✓ R-0001/AC2  tests/covers.js:7"
    assert R.summary([r]) == "1 reviewed · 1 passed. Suffixes written for the 1 that passed."


def test_a_reject_leaves_the_file_and_reports_the_reasons(tmp_path):
    d = project(tmp_path)
    before = read(d)
    [r] = reviewed(d, FakeJudge(verdicts("reject", ["asserts the status but not the body"])))
    assert read(d) == before
    assert R.line(r, PAINT) == "✗ R-0001/AC2  tests/covers.js:7  rejected"
    assert R.summary([r]) == "1 reviewed · 1 rejected."
    assert R.details(r, 100, PAINT) == [
        "    Criterion  token inside the 30s clock-skew window -> accepted [http]",
        "    Review     (no review yet)",
        "      • asserts the status but not the body"]


def test_unclear_leaves_the_file_and_reports_the_question(tmp_path):
    d = project(tmp_path)
    before = read(d)
    [r] = reviewed(d, FakeJudge(verdicts("unclear", question="Is a 30s skew inclusive?")))
    assert read(d) == before
    assert R.line(r, PAINT) == "? R-0001/AC2  tests/covers.js:7  unclear"
    assert "    Question   Is a 30s skew inclusive?" in R.details(r, 100, PAINT)


@pytest.mark.parametrize("reply", [
    "Looks good to me.",
    "[{\"ac\": \"R-0001/AC2\", \"verdict\": \"pass\"}]",      # no lists: not a pass
    "[{\"ac\": \"R-0009/AC1\", \"covered\": [], \"comments\": []}]",
    "[not json]",
])
def test_an_answer_that_does_not_parse_is_an_error(tmp_path, reply):
    d = project(tmp_path)
    before = read(d)
    [r] = reviewed(d, FakeJudge(lambda p: reply))
    assert read(d) == before
    assert R.line(r, PAINT) == "! R-0001/AC2  tests/covers.js:7  error"


def test_a_failing_judge_is_an_error(tmp_path):
    d = project(tmp_path)

    def boom(prompt):
        raise RuntimeError("no credentials")
    [r] = reviewed(d, FakeJudge(boom))
    assert r["verdict"] == "error" and "no credentials" in r["comments"][0]["text"]


def test_a_fenced_json_answer_parses():
    reply = ("```json\n[{\"ac\": \"R-0001/AC1\", \"covered\": [\"a 401\"], "
             "\"comments\": []}]\n```")
    assert R.parse_first(reply, ["R-0001/AC1"]) == {
        "R-0001/AC1": {"covered": ["a 401"], "comments": [], "advice": [],
                       "question": ""}}


def test_nothing_to_review_asks_nobody(tmp_path):
    d = project(tmp_path)
    stamp(d)
    judge = FakeJudge(verdicts("pass"))
    assert reviewed(d, judge) == []
    assert judge.prompts == []
    assert R.summary([]) == "nothing needed review"


def test_a_result_holds_the_criterion_its_tests_and_the_review(tmp_path):
    d = project(tmp_path)
    assert reviewed(d, FakeJudge(verdicts("reject", ["weak"]))) == [
        {"ac": "R-0001/AC2",
         "criterion": "token inside the 30s clock-skew window -> accepted [http]",
         "tests": [{"file": "tests/covers.js", "line": 7, "state": "no review yet"}],
         "file": "tests/covers.js", "line": 7,
         "state": "no review yet", "verdict": "reject", "covered": [],
         "comments": [{"check": "clause-coverage", "text": "weak"}],
         "advice": [], "resolved": [], "question": "", "tokens": 0}]


def test_there_is_no_review_command():
    """Build drives the review until its tests converge: run on its own, it
    would only ever find nothing to review."""
    from hamilton_core import cli
    with pytest.raises(SystemExit):
        cli.main(["review"])


def test_the_prompt_holds_the_spec_and_the_test_but_no_implementation(tmp_path):
    d = project(tmp_path)
    os.makedirs(f"{d}/src")
    open(f"{d}/src/validator.js", "w").write("const SKEW = 30; // IMPLEMENTATION\n")
    judge = FakeJudge(verdicts("pass"))
    reviewed(d, judge)
    [prompt] = judge.prompts
    assert "The token validator rejects a request whose exp claim is in the past." in prompt
    assert "AC2: token inside the 30s clock-skew window -> accepted [http]" in prompt
    assert "**http** — requests to the running service; external services stubbed." in prompt
    assert "### `tests/covers.js`" in prompt
    assert "import { get } from './support.js';" in prompt           # the preamble
    assert "accepts a token inside the skew window" in prompt        # its section
    assert "rejects an expired token" not in prompt                  # not AC1's
    assert "IMPLEMENTATION" not in prompt


def test_a_test_tagged_for_two_criteria_is_judged_with_each(tmp_path):
    """The criterion is the unit: a test that proves two is part of both sets."""
    d = project(tmp_path)
    open(f"{d}/tests/covers.js", "w").write(
        "// @covers R-0001/AC1\n// @covers R-0001/AC2\nit('both', ...)\n")
    judge = FakeJudge(verdicts("pass"))
    assert {r["verdict"] for r in reviewed(d, judge)} == {"pass"}
    assert len(judge.prompts) == 2
    assert all("it('both', ...)" in p for p in judge.prompts)
    assert run_verify(d).returncode == 0


def test_a_criterion_s_tests_are_judged_together_wherever_they_lie(tmp_path):
    """Several tests can share a criterion's cases. Judged one by one, each
    would be faulted for the cases the others cover."""
    d = project(tmp_path)
    open(f"{d}/tests/more.js", "w").write(
        "import { skew } from './support.js';\n\n"
        "// @covers R-0001/AC2\nit('accepts 29s of skew', ...)\n\n"
        "// @covers R-0001/AC2\nit('accepts exactly 30s of skew', ...)\n")
    judge = FakeJudge(verdicts("pass"))
    assert [r["verdict"] for r in reviewed(d, judge)] == ["pass"]
    [prompt] = judge.prompts
    for text in ("accepts a token inside the skew window",   # tests/covers.js
                 "accepts 29s of skew", "accepts exactly 30s of skew"):
        assert text in prompt
    # each file's preamble once, not once per test
    assert prompt.count("import { skew } from './support.js';") == 1
    assert "Judge them **together**" in prompt
    assert run_verify(d).returncode == 0


def test_writing_a_suffix_keeps_line_endings_and_replaces_an_old_one(tmp_path):
    from hamilton_core.verify import Tag
    (tmp_path / "t.js").write_bytes(
        b"// @covers R-0001/AC1 #000000.000000\r\nit('x')\r\n// @covers R-0001/AC2\r\n")
    R.write_suffix(str(tmp_path), Tag("R-0001", "AC1", "t.js", 1, "000000.000000"),
                   "aaaaaa.bbbbbb")
    R.write_suffix(str(tmp_path), Tag("R-0001", "AC2", "t.js", 3, None), "cccccc.dddddd")
    assert (tmp_path / "t.js").read_bytes() == (
        b"// @covers R-0001/AC1 #aaaaaa.bbbbbb\r\nit('x')\r\n"
        b"// @covers R-0001/AC2 #cccccc.dddddd\r\n")


def test_reviewers_run_side_by_side_up_to_the_cap_and_results_keep_file_order(tmp_path):
    d = project(tmp_path)
    with open(f"{d}/spec/requirements.md", "a") as fh:
        fh.write("\n## R-0002\nActor: A-0001\nStatement: The service answers health "
                 "checks.\nCriteria:\n"
                 + "".join(f"- AC{i + 1}: probe {i} -> 200 [http]\n" for i in range(6)))
    for i in range(6):
        open(f"{d}/tests/t{i}.js", "w").write(f"// @covers R-0002/AC{i + 1}\nit('t{i}', ...)\n")
    running, peak = [0], [0]

    class SlowJudge(FakeJudge):
        async def ask(self, prompt, on_tokens=None):
            running[0] += 1
            peak[0] = max(peak[0], running[0])
            await asyncio.sleep(0.01)
            running[0] -= 1
            return await super().ask(prompt)

    results = reviewed(d, SlowJudge(verdicts("pass")))
    assert peak[0] == R.PARALLEL
    assert [r["file"] for r in results] == \
        ["tests/covers.js"] + [f"tests/t{i}.js" for i in range(6)]
    assert run_verify(d).returncode == 0


def test_the_watcher_hears_each_criterion_start_and_finish(tmp_path):
    d = project(tmp_path)
    open(f"{d}/tests/other.js", "w").write("// @covers R-0001/AC1\nit('x', ...)\n")
    heard = []

    class Watch(R.Watch):
        def started(self, key, label):
            heard.append(("started", label))

        def finished(self, key, results):
            heard.append(("finished", [r["ac"] for r in results]))

    reviewed(d, FakeJudge(verdicts("pass")), watch=Watch())
    assert sorted(heard) == [("finished", ["R-0001/AC1"]), ("finished", ["R-0001/AC2"]),
                             ("started", "R-0001/AC1 · covers.js, other.js"),
                             ("started", "R-0001/AC2 · covers.js")]


def test_a_long_reason_wraps_under_its_bullet():
    from hamilton_core.session.console import Paint
    r = {"ac": "R-0001/AC2", "criterion": "short", "file": "t.js", "line": 1,
         "state": "test changed", "verdict": "reject", "question": "",
         "comments": [{"check": "scope",
                       "text": "one two three four five six seven eight nine ten"}]}
    assert R.details(r, 30, Paint(False)) == [
        "    Criterion  short",
        "    Review     (test changed)",
        "      • one two three four",
        "        five six seven eight",
        "        nine ten",
    ]


# --- a review inside `hamilton build` ----------------------------------------

def shown():
    """A `Shown` over a console that captures what it prints."""
    import io

    from hamilton_core.session.console import Console
    out = io.StringIO()
    return R.Shown(Console(out=out, inp=io.StringIO(), color=False)), out


def results(*specs):
    """One result per (ac, file, verdict[, reasons, question])."""
    out = []
    for ac, path, verdict, *rest in specs:
        reasons, question = (list(rest) + [[], ""])[:2]
        out.append({"ac": ac, "criterion": "the criterion's text", "file": path,
                    "line": 1, "state": "no review yet", "verdict": verdict,
                    "comments": [{"check": "", "text": r} for r in reasons],
                    "question": question})
    return out


def test_the_session_report_unfolds_only_what_stops_the_loop(tmp_path):
    s, out = shown()
    s.report(results(
        ("R-0001/AC1", "t.js", "pass"),
        ("R-0001/AC2", "t.js", "reject", ["asserts the status but not the body"]),
        ("R-0001/AC3", "t.js", "unclear", [], "Is a skew of exactly 30s inside it?")))
    text = out.getvalue()
    assert "3 reviewed · 1 passed · 1 rejected · 1 unclear." in text
    # the unclear is about to become a hard stop, so it is unfolded here,
    # under the tag it belongs to ...
    assert "? R-0001/AC3  t.js:1  unclear" in text
    assert "Question   Is a skew of exactly 30s inside it?" in text
    # ... while the reject is the agent's next piece of work, not the engineer's
    assert "asserts the status but not the body" not in text


def test_a_big_review_reports_a_line_per_file_not_per_tag():
    """45 rejected tags printed 45 lines, which is the wall this replaces."""
    s, out = shown()
    s.report(results(*[(f"R-0010/AC{i}", "tests/Browser/wizard.test.js", "reject")
                       for i in range(1, 16)],
                     *[(f"R-0020/AC{i}", "tests/Php/EnvTest.php", "unclear", [],
                        "which?") for i in range(1, 4)],
                     ("R-0002/AC1", "tests/Php/EnvTest.php", "pass")))
    lines = [ln for ln in out.getvalue().splitlines() if ln.startswith("  ")]
    assert lines[:3] == ["  ✓ 1 passed",
                         "  ✗ tests/Browser/wizard.test.js  15 rejected",
                         "  ? tests/Php/EnvTest.php  3 unclear"]
    assert "19 reviewed · 1 passed · 15 rejected · 3 unclear." in lines[3]


def test_what_did_not_pass_is_offered_once_and_stays_as_it_was_left():
    s, out = shown()
    s.report(results(("R-0001/AC1", "t.js", "pass"),
                     ("R-0001/AC2", "t.js", "reject", ["too narrow"])))
    s.browse()                       # no terminal: everything prints unfolded
    text = out.getvalue()
    assert "1 need attention:" in text and "• too narrow" in text
    s.browse()                       # ... and only the once
    assert out.getvalue().count("need attention") == 1


# --- a review that is settled, not re-judged ---------------------------------

EARLIER = {"covered": ["asserts a 401 for an expired token"],
           "comments": [{"check": "clause-coverage", "text": "the body is never checked"},
                        {"check": "scope", "text": "only one expired token is tried"}]}


def settled(k1=True, c1=True, c2=True, question=""):
    return {"kept": {"K1": {"ok": k1, "why": "" if k1 else "the 401 check went"}},
            "resolved": {"C1": {"ok": c1, "why": "" if c1 else "still not checked",
                                "covers": "asserts the body holds no user data"},
                         "C2": {"ok": c2, "why": "" if c2 else "still one token",
                                "covers": "tries every expired token"}},
            "question": question}


def test_the_verdict_is_computed_not_answered():
    assert R.verdict([], "") == "pass"
    assert R.verdict([{"check": "scope", "text": "x"}], "") == "reject"
    assert R.verdict([], "Which method verifies it?") == "unclear"


def test_everything_settled_passes_and_coverage_grows():
    r = R.settle(EARLIER, settled())
    assert r["verdict"] == "pass" and r["comments"] == []
    assert r["covered"] == ["asserts a 401 for an expired token",
                            "asserts the body holds no user data",
                            "tries every expired token"]
    assert r["resolved"] == ["the body is never checked",
                             "only one expired token is tried"]


def test_an_unresolved_comment_stays_open_with_why():
    r = R.settle(EARLIER, settled(c2=False))
    assert r["verdict"] == "reject"
    assert [(c["text"], c["why"]) for c in r["comments"]] == [
        ("only one expired token is tried", "still one token")]


def test_a_comment_keeps_its_text_round_after_round():
    """Only the latest reason it is still open changes -- the text the next
    reviewer settles never grows."""
    once = R.settle(EARLIER, settled(c2=False))
    earlier = {"covered": once["covered"], "comments": once["comments"]}
    again = {"kept": {k: {"ok": True, "why": ""} for k in R.points(earlier)[0]},
             "resolved": {"C1": {"ok": False, "why": "two tokens now, still not all"}},
             "question": ""}
    twice = R.settle(earlier, again)
    assert [(c["text"], c["why"]) for c in twice["comments"]] == [
        ("only one expired token is tried", "two tokens now, still not all")]


def test_a_covered_point_the_revision_lost_reopens_as_a_comment():
    """The ratchet: a revision that fixes the comments but drops what the test
    already proved has not fixed the test."""
    r = R.settle(EARLIER, settled(k1=False))
    assert r["verdict"] == "reject"
    assert r["comments"][0]["text"] == "no longer covers: asserts a 401 for an expired token"
    assert r["comments"][0]["why"] == "the 401 check went"


def test_a_re_review_may_not_add_or_skip_a_point():
    ok = json.dumps([{"ac": "R-0001/AC1", **settled()}])
    assert R.parse_settle(ok, {"R-0001/AC1": EARLIER})
    added = settled()
    added["resolved"]["C3"] = {"ok": False, "why": "a new thought"}
    with pytest.raises(ValueError, match="adds a comment"):
        R.parse_settle(json.dumps([{"ac": "R-0001/AC1", **added}]),
                       {"R-0001/AC1": EARLIER})
    skipped = settled()
    del skipped["kept"]["K1"]
    with pytest.raises(ValueError, match="leaves a covered point unsettled"):
        R.parse_settle(json.dumps([{"ac": "R-0001/AC1", **skipped}]),
                       {"R-0001/AC1": EARLIER})


def settling(verdict_of):
    """A judge that answers a first review with EARLIER's lists, and settles a
    re-review with `verdict_of(round)` -- a `settled(...)` answer."""
    rounds = []

    def reply(prompt):
        acs = dict.fromkeys(re.findall(r"^## (R-\d{4}/AC\d+)$", prompt, re.M))
        if "Comments to settle" not in prompt:
            return json.dumps([{"ac": ac, **EARLIER, "question": ""} for ac in acs])
        rounds.append(prompt)
        return json.dumps([{"ac": ac, **verdict_of(len(rounds))} for ac in acs])
    return reply, rounds


def test_the_memory_turns_the_next_review_into_a_re_review(tmp_path):
    d = project(tmp_path)
    reply, rounds = settling(lambda n: settled(c2=False))
    judge = FakeJudge(reply)
    first = reviewed(d, judge)
    assert first[0]["verdict"] == "reject" and rounds == []
    memory = R.remember({}, first)
    assert memory == {"R-0001/AC2": {
        "covered": EARLIER["covered"], "comments": EARLIER["comments"], "advice": []}}

    again = reviewed(d, judge, memory=memory)
    assert len(rounds) == 1
    # the re-review holds the numbered points, and still no implementation
    assert "K1: asserts a 401 for an expired token" in rounds[0]
    assert "C2: only one expired token is tried" in rounds[0]
    assert "Comments to settle" in rounds[0] and "src/" not in rounds[0]
    assert again[0]["verdict"] == "reject"
    assert [(c["text"], c["why"]) for c in again[0]["comments"]] == [
        ("only one expired token is tried", "still one token")]


def test_a_criterion_that_passes_is_forgotten_and_a_changed_one_too():
    memory = {"R-0001/AC1": EARLIER, "R-0001/AC2": EARLIER}
    after = R.remember(memory, [{"ac": "R-0001/AC1", "verdict": "pass"}])
    assert list(after) == ["R-0001/AC2"]
    assert R.forget(memory, ["R-0001/AC2"]) == {"R-0001/AC1": EARLIER}


def test_adding_a_test_keeps_the_criterion_s_review(tmp_path):
    """The memory belongs to the criterion, not to a position in a file: a
    revision that adds or moves a test is settled against the same list."""
    d = project(tmp_path)
    reply, rounds = settling(lambda n: settled(c2=False))
    judge = FakeJudge(reply)
    memory = R.remember({}, reviewed(d, judge))
    body = read(d)
    open(f"{d}/tests/covers.js", "w").write(      # a new test, above the old one
        body.replace("// @covers R-0001/AC2",
                     "// @covers R-0001/AC2\nit('a new case', ...)\n\n// @covers R-0001/AC2"))
    reviewed(d, judge, memory=memory)
    [again] = rounds
    assert "C2: only one expired token is tried" in again
    assert "it('a new case', ...)" in again


def test_an_unclear_criterion_stays_unclear_until_its_question_is_answered(
        tmp_path):
    """A run that ends at the question leaves nothing to settle. The next
    re-review must not read that empty list as a pass and write the suffix."""
    d = project(tmp_path)
    before = read(d)
    asking = FakeJudge(verdicts("unclear", question="Is a 30s skew inclusive?"))
    memory = R.remember({}, reviewed(d, asking))
    assert memory["R-0001/AC2"]["question"] == "Is a 30s skew inclusive?"

    settling_nothing = FakeJudge(lambda prompt: json.dumps(
        [{"ac": "R-0001/AC2", "kept": {}, "resolved": {}, "question": ""}]))
    [again] = reviewed(d, settling_nothing, memory=memory)
    assert again["verdict"] == "unclear"
    assert again["question"] == "Is a 30s skew inclusive?"
    assert read(d) == before


def test_a_remembered_review_with_nothing_to_settle_is_reviewed_afresh(
        tmp_path):
    d = project(tmp_path)
    judge = FakeJudge(verdicts("reject", ["the body is never checked"]))
    [r] = reviewed(d, judge, memory={
        "R-0001/AC2": {"covered": [], "comments": []}})
    assert "Comments to settle" not in judge.prompts[0]
    assert r["verdict"] == "reject"


def test_the_prompt_holds_the_spec_files_the_criterion_references(tmp_path):
    """The reviewer has no tools: a referenced file it is not shown is one it
    cannot hold the test to."""
    d = project(tmp_path)
    body = open(f"{d}/spec/requirements.md").read()
    open(f"{d}/spec/requirements.md", "w").write(body.replace(
        "-> accepted [http]", "-> accepted per spec/skew.md, drawn in spec/skew.png [http]"))
    open(f"{d}/spec/skew.md", "w").write("Clock skew of up to 30 seconds is tolerated.\n")
    open(f"{d}/spec/skew.png", "wb").write(b"\x89PNG\xff\x00")
    judge = FakeJudge(verdicts("pass"))
    reviewed(d, judge)
    [prompt] = [p for p in judge.prompts if "## R-0001/AC2" in p]
    assert "### `spec/skew.md`" in prompt
    assert "Clock skew of up to 30 seconds is tolerated." in prompt
    assert "### `spec/skew.png`\n\n(not text, not shown)" in prompt


# --- advice never blocks ------------------------------------------------------

def advises(prompt):
    return json.dumps([{"ac": ac, "covered": ["asserts the outcome"], "comments": [],
                        "advice": ["a boundary case would pin it down"], "question": ""}
                       for ac in re.findall(r"^## (R-\d{4}/AC\d+)$", prompt, re.M)])


def test_advice_alone_passes_and_writes_the_suffix(tmp_path):
    d = project(tmp_path)
    [r] = reviewed(d, FakeJudge(advises))
    assert r["verdict"] == "pass"
    assert r["advice"] == ["a boundary case would pin it down"]
    assert run_verify(d).returncode == 0


def test_advice_is_shown_but_never_settled(tmp_path):
    """A re-review settles the blocking comments only: advice cannot keep a
    criterion in rewrites."""
    earlier = dict(EARLIER, advice=["name the helper for what it checks"])
    d = project(tmp_path)
    judge = FakeJudge(lambda p: json.dumps([{"ac": "R-0001/AC2", **settled()}]))
    [r] = reviewed(d, judge, memory={"R-0001/AC2": earlier})
    [prompt] = judge.prompts
    assert "name the helper" not in prompt
    assert r["verdict"] == "pass" and r["advice"] == earlier["advice"]
    shown = "\n".join(R.details(r, 100, PAINT))
    assert "advice, not required: name the helper for what it checks" in shown


def test_a_writer_is_not_sent_to_act_on_advice():
    from hamilton_core import build as B
    reqs = {"R-0001": {"title": "", "statement": "s.",
                       "acs": {"AC1": {"text": "a -> b [unit]", "methods": ["unit"]}}}}
    review = {"tests": [{"file": "t.js", "line": 1}], "covered": [],
              "comments": [{"check": "can-fail", "text": "loose assertion"}],
              "advice": ["rename the helper"]}
    text = B.test_prompt("R-0001/AC1", reqs, {"unit": {"description": "x"}}, "",
                         {"unit": ["tests"]}, review)
    assert "loose assertion" in text and "rename the helper" not in text


# --- tests too long to review ------------------------------------------------

def long_preamble(d, lines):
    """AC2's file grows `lines` of helpers above its tests."""
    body = read(d)
    open(f"{d}/tests/covers.js", "w").write(
        "".join(f"const helper{n} = () => {n};\n" for n in range(lines)) + body)


def test_tests_too_long_are_sent_back_without_asking_the_reviewer(tmp_path):
    d = project(tmp_path)
    long_preamble(d, R.MAX_LINES)

    class Unasked(FakeJudge):
        async def ask(self, prompt, on_tokens=None):
            raise AssertionError("the reviewer was asked")

    [r] = [r for r in reviewed(d, Unasked(None)) if r["ac"] == "R-0001/AC2"]
    assert r["verdict"] == "reject" and r["tokens"] == 0
    [comment] = r["comments"]
    assert comment["check"] == "size"
    assert f"more than the {R.MAX_LINES} a criterion may take" in comment["text"]
    assert "#" not in next(ln for ln in read(d).splitlines() if "R-0001/AC2" in ln)


def test_tests_within_the_cap_are_reviewed(tmp_path):
    d = project(tmp_path)
    long_preamble(d, 10)
    judge = FakeJudge(verdicts("pass"))
    assert {r["verdict"] for r in reviewed(d, judge)} == {"pass"}
    assert judge.prompts


def test_a_reject_for_length_is_no_review_and_keeps_what_was_open():
    earlier = {"covered": ["asserts a 401"], "advice": [],
               "comments": [{"check": "scope", "text": "only one token"}]}
    r = dict(R.too_long(500, earlier), ac="R-0001/AC1")
    assert [c["text"] for c in r["comments"]][0] == "only one token"
    assert r["covered"] == ["asserts a 401"]
    assert not R.reviewed(r)
    # nothing of it is remembered: the review it stands in for never happened
    assert R.remember({"R-0001/AC1": earlier}, [r]) == {"R-0001/AC1": earlier}
    assert R.remember({}, [r]) == {}


def test_a_writer_sent_back_for_length_is_told_its_tests_were_not_read():
    from hamilton_core import build as B
    reqs = {"R-0001": {"title": "", "statement": "s.",
                       "acs": {"AC1": {"text": "a -> b [unit]", "methods": ["unit"]}}}}
    review = dict(R.too_long(500), tests=[{"file": "t.js", "line": 1}])
    text = B.test_prompt("R-0001/AC1", reqs, {"unit": {"description": "x"}}, "",
                         {"unit": ["tests"]}, review)
    assert "sent back unread, for their length" in text
    assert "The next review checks only these points" not in text
    assert f"At most {R.MAX_LINES} lines" in text
