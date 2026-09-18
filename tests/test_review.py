"""`hamilton review` -- the test reviewer (D-020), driven by a fake `Judge`.

Nothing here reaches a model: the judge is scripted, and what is pinned is
what Hamilton does around it -- which tags it sends, what the prompt holds
(and does not), how verdicts land in the files, and how they are reported.
"""

import json
import os
import re

import pytest

from conftest import copy_fixture, run_check, stamp

from hamilton_core import review as R

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

    async def ask(self, prompt):
        self.prompts.append(prompt)
        return self.reply(prompt)


def verdicts(verdict, reasons=(), question=""):
    """A reply giving ``verdict`` to every AC the prompt names."""
    def reply(prompt):
        acs = dict.fromkeys(re.findall(r"^## (R-\d{4}/AC\d+)$", prompt, re.M))
        return json.dumps([{"ac": ac, "verdict": verdict, "reasons": list(reasons),
                            "question": question} for ac in acs])
    return reply


def project(tmp_path, monkeypatch, phase="build"):
    """The clean fixture with a two-test file, AC1 reviewed and AC2 not."""
    d = copy_fixture("clean", tmp_path)
    open(f"{d}/.hamilton/phase", "w").write(phase)
    open(f"{d}/tests/covers.js", "w").write(WEB_TEST)
    stamp(d)
    body = open(f"{d}/tests/covers.js").read()
    ac2 = next(ln for ln in body.splitlines() if "R-0001/AC2" in ln)
    open(f"{d}/tests/covers.js", "w").write(body.replace(ac2, "// @covers R-0001/AC2"))
    monkeypatch.chdir(d)
    return d


def read(d, rel="tests/covers.js"):
    return open(os.path.join(d, rel)).read()


def test_a_pass_writes_exactly_that_tags_suffix(tmp_path, monkeypatch, capsys):
    d = project(tmp_path, monkeypatch)
    before = read(d)
    judge = FakeJudge(verdicts("pass"))
    assert R.main(judge=judge) == 0
    after = read(d)
    changed = [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
    assert len(changed) == 1 and len(judge.prompts) == 1
    old, new = changed[0]
    assert old == "// @covers R-0001/AC2"
    assert new.startswith("// @covers R-0001/AC2 #") and len(new) == len(old) + 15
    assert run_check(d).returncode == 0
    assert "R-0001/AC2: pass (no review yet) -- suffix written" in capsys.readouterr().out


def test_a_reject_leaves_the_file_and_reports_the_reasons(tmp_path, monkeypatch, capsys):
    d = project(tmp_path, monkeypatch)
    before = read(d)
    judge = FakeJudge(verdicts("reject", ["asserts the status but not the body"]))
    assert R.main(judge=judge) == 1
    assert read(d) == before
    out = capsys.readouterr().out
    assert "R-0001/AC2: reject" in out and "  - asserts the status but not the body" in out


def test_unclear_leaves_the_file_and_reports_the_question(tmp_path, monkeypatch, capsys):
    d = project(tmp_path, monkeypatch)
    before = read(d)
    judge = FakeJudge(verdicts("unclear", question="Is a 30s skew inclusive?"))
    assert R.main(judge=judge) == 1
    assert read(d) == before
    assert "question: Is a 30s skew inclusive?" in capsys.readouterr().out


@pytest.mark.parametrize("reply", [
    "Looks good to me.",
    "[{\"ac\": \"R-0001/AC2\", \"verdict\": \"fine\"}]",
    "[{\"ac\": \"R-0009/AC1\", \"verdict\": \"pass\"}]",
    "[not json]",
])
def test_an_answer_that_does_not_parse_is_an_error(tmp_path, monkeypatch, capsys, reply):
    d = project(tmp_path, monkeypatch)
    before = read(d)
    assert R.main(judge=FakeJudge(lambda p: reply)) == 1
    assert read(d) == before
    assert "R-0001/AC2: error" in capsys.readouterr().out


def test_a_failing_judge_is_an_error(tmp_path, monkeypatch, capsys):
    project(tmp_path, monkeypatch)

    def boom(prompt):
        raise RuntimeError("no credentials")
    assert R.main(judge=FakeJudge(boom)) == 1
    assert "no credentials" in capsys.readouterr().out


def test_a_fenced_json_answer_parses():
    reply = "```json\n[{\"ac\": \"R-0001/AC1\", \"verdict\": \"pass\"}]\n```"
    assert R.parse(reply, ["R-0001/AC1"]) == {
        "R-0001/AC1": {"verdict": "pass", "reasons": [], "question": ""}}


def test_the_ac_filter_reviews_only_that_criterion(tmp_path, monkeypatch):
    d = project(tmp_path, monkeypatch)
    open(f"{d}/tests/other.js", "w").write("// @covers R-0001/AC1\nit('x', ...)\n")
    judge = FakeJudge(verdicts("pass"))
    assert R.main("R-0001/AC1", judge=judge) == 0
    assert len(judge.prompts) == 1 and "tests/other.js" in judge.prompts[0]
    assert "// @covers R-0001/AC2\n" in read(d)             # left for later


@pytest.mark.parametrize("only", ["R-0001", "R-0001/AC9"])
def test_a_bad_ac_filter_is_a_usage_error(tmp_path, monkeypatch, only):
    project(tmp_path, monkeypatch)
    assert R.main(only, judge=FakeJudge(verdicts("pass"))) == 2


def test_nothing_to_review_exits_zero_without_asking(tmp_path, monkeypatch, capsys):
    d = project(tmp_path, monkeypatch)
    stamp(d)
    judge = FakeJudge(verdicts("pass"))
    assert R.main(judge=judge) == 0
    assert judge.prompts == []
    assert "nothing needed review" in capsys.readouterr().err


def test_spec_phase_exits_2(tmp_path, monkeypatch, capsys):
    project(tmp_path, monkeypatch, phase="spec")
    judge = FakeJudge(verdicts("pass"))
    assert R.main(judge=judge) == 2
    assert judge.prompts == [] and "not 'build'" in capsys.readouterr().err


def test_json_output(tmp_path, monkeypatch, capsys):
    project(tmp_path, monkeypatch)
    assert R.main(as_json=True, judge=FakeJudge(verdicts("reject", ["weak"]))) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": False, "results": [
        {"ac": "R-0001/AC2", "file": "tests/covers.js", "line": 7,
         "state": "no review yet", "verdict": "reject", "reasons": ["weak"],
         "question": ""}]}


def test_json_usage_error(tmp_path, monkeypatch, capsys):
    project(tmp_path, monkeypatch, phase="spec")
    assert R.main(as_json=True, judge=FakeJudge(verdicts("pass"))) == 2
    assert "error" in json.loads(capsys.readouterr().out)


def test_the_prompt_holds_the_spec_and_the_test_but_no_implementation(tmp_path, monkeypatch):
    d = project(tmp_path, monkeypatch)
    os.makedirs(f"{d}/src")
    open(f"{d}/src/validator.js", "w").write("const SKEW = 30; // IMPLEMENTATION\n")
    judge = FakeJudge(verdicts("pass"))
    R.main(judge=judge)
    [prompt] = judge.prompts
    assert "The token validator rejects a request whose exp claim is in the past." in prompt
    assert "AC2: token inside the 30s clock-skew window -> accepted [http]" in prompt
    assert "**http** — requests to the running service; external services stubbed." in prompt
    assert "File: `tests/covers.js`" in prompt
    assert "import { get } from './support.js';" in prompt           # the preamble
    assert "accepts a token inside the skew window" in prompt        # its section
    assert "rejects an expired token" not in prompt                  # not AC1's
    assert "IMPLEMENTATION" not in prompt


def test_stacked_tags_are_one_region_and_one_call(tmp_path, monkeypatch):
    d = project(tmp_path, monkeypatch)
    open(f"{d}/tests/covers.js", "w").write(
        "// @covers R-0001/AC1\n// @covers R-0001/AC2\nit('both', ...)\n")
    judge = FakeJudge(verdicts("pass"))
    assert R.main(judge=judge) == 0
    assert len(judge.prompts) == 1
    assert "## R-0001/AC1" in judge.prompts[0] and "## R-0001/AC2" in judge.prompts[0]
    assert run_check(d).returncode == 0


def test_writing_a_suffix_keeps_line_endings_and_replaces_an_old_one(tmp_path):
    from hamilton_core.check import Tag
    (tmp_path / "t.js").write_bytes(
        b"// @covers R-0001/AC1 #000000.000000\r\nit('x')\r\n// @covers R-0001/AC2\r\n")
    R.write_suffix(str(tmp_path), Tag("R-0001", "AC1", "t.js", 1, "000000.000000"),
                   "aaaaaa.bbbbbb")
    R.write_suffix(str(tmp_path), Tag("R-0001", "AC2", "t.js", 3, None), "cccccc.dddddd")
    assert (tmp_path / "t.js").read_bytes() == (
        b"// @covers R-0001/AC1 #aaaaaa.bbbbbb\r\nit('x')\r\n"
        b"// @covers R-0001/AC2 #cccccc.dddddd\r\n")


def test_the_cli_routes_review(tmp_path, monkeypatch):
    project(tmp_path, monkeypatch, phase="spec")
    from hamilton_core import cli
    assert cli.main(["review", "--json"]) == 2
