"""`hamilton validate` -- what Hamilton does around the conversation.

The conversation itself is the session driver's (`test_session.py`). What is
pinned here is Hamilton's side of it: when a spec change may start and when
it is over, and that the build runs exactly when `hamilton verify` says there
is something to build -- never for a presentation fix.
"""

import asyncio
import io
import subprocess

from conftest import copy_fixture, stamp
from test_build import Answering, FakeJudge, FakeWorker, passes

from hamilton_core import phase
from hamilton_core import validate as V
from hamilton_core.session.modes import VALIDATE


def project(tmp_path, start="start_command=true\n"):
    """The clean fixture, green, in build phase, under git."""
    d = copy_fixture("clean", tmp_path)
    with open(f"{d}/.hamilton/config", "a") as fh:
        fh.write(start)
    stamp(d)
    phase.write(d, "build")
    subprocess.run(["git", "init", "-q", d], check=True)
    return d


def validation(d, keys=""):
    out = io.StringIO()
    console = Answering(out=out, inp=io.StringIO(keys), color=False)
    worker, judge = FakeWorker(), FakeJudge(passes)
    return V.Validation(d, console, worker, judge), worker, judge, out


def edit_a_test(d):
    """What a bug fix leaves: the criterion's test changed, so unreviewed."""
    with open(f"{d}/tests/covers.js", "a") as fh:
        fh.write("// the case the engineer found\n")


# --- when validation may start -------------------------------------------------

def test_validation_needs_something_to_run(tmp_path):
    d = project(tmp_path, start="")
    assert "start_command" in VALIDATE.precheck(d)


def test_validation_starts_from_a_green_gate(tmp_path):
    d = project(tmp_path)
    assert VALIDATE.precheck(d) is None
    edit_a_test(d)
    assert "hamilton build" in VALIDATE.precheck(d)


# --- a spec change -----------------------------------------------------------------

def test_a_spec_change_switches_the_phase_on_the_engineers_word(tmp_path):
    d = project(tmp_path)
    v, *_ , out = validation(d, keys="1\n")
    told = asyncio.run(v.change_spec({"finding": "no way to log out",
                                      "change": "a requirement for logging out"}))
    assert phase.read(d) == "spec"
    assert "review protocol" in told and "a requirement for logging out" in told
    assert "no way to log out" in out.getvalue()


def test_a_declined_spec_change_changes_nothing(tmp_path):
    d = project(tmp_path)
    v, *_ = validation(d, keys="2\n")
    told = asyncio.run(v.change_spec({"finding": "f", "change": "c"}))
    assert phase.read(d) == "build"
    assert "Do not work around it" in told


def test_while_the_spec_is_being_changed_nothing_is_built(tmp_path):
    d = project(tmp_path)
    phase.write(d, "spec")
    edit_a_test(d)
    v, worker, judge, _ = validation(d)
    assert asyncio.run(v.after_turn(False)) is None
    assert phase.read(d) == "spec" and judge.asked == []


def test_a_finished_spec_change_goes_back_to_build_and_is_built(tmp_path):
    d = project(tmp_path)
    phase.write(d, "spec")
    edit_a_test(d)                  # as a reworded criterion would leave it
    v, worker, judge, _ = validation(d)
    told = asyncio.run(v.after_turn(True))
    assert phase.read(d) == "build"
    assert judge.asked                              # the build reviewed it
    assert "'build' again" in told and "gate is green" in told


def test_a_spec_change_that_leaves_nothing_to_build_says_so(tmp_path):
    d = project(tmp_path)
    phase.write(d, "spec")
    v, worker, judge, _ = validation(d)
    told = asyncio.run(v.after_turn(True))
    assert phase.read(d) == "build"
    assert "nothing to build" in told and judge.asked == []


def test_a_changed_design_guide_is_built_though_the_gate_is_green(tmp_path):
    d = project(tmp_path)
    phase.write(d, "spec")
    with open(f"{d}/spec/design-guide.md", "w") as fh:
        fh.write("# Design guide\n\nCalm, one accent colour.\n")
    v, worker, judge, _ = validation(d)
    told = asyncio.run(v.after_turn(True))
    assert worker.of("present") and "gate is green" in told


# --- a bug, and a presentation fix ---------------------------------------------------

def test_a_new_test_is_built_before_the_engineer_goes_on(tmp_path):
    d = project(tmp_path)
    edit_a_test(d)
    v, worker, judge, _ = validation(d)
    told = asyncio.run(v.after_turn(True))
    assert judge.asked and "gate is green" in told


def test_a_presentation_fix_costs_no_build(tmp_path):
    d = project(tmp_path)
    with open(f"{d}/page.css", "w") as fh:
        fh.write("h1 { color: teal; }\n")
    v, worker, judge, _ = validation(d)
    assert asyncio.run(v.after_turn(True)) is None
    assert judge.asked == [] and worker.prompts == []


# --- the end of the session ----------------------------------------------------------

def test_a_session_that_changed_nothing_ends_without_a_build(tmp_path):
    d = project(tmp_path)
    v, worker, judge, out = validation(d)
    assert asyncio.run(v.at_end()) is None
    assert "building" not in out.getvalue()


def test_a_session_that_changed_the_project_ends_with_the_suite(tmp_path):
    d = project(tmp_path)
    v, worker, judge, out = validation(d)
    with open(f"{d}/page.css", "w") as fh:
        fh.write("h1 { color: teal; }\n")
    assert asyncio.run(v.at_end()) == 0
    assert "the gate is green" in out.getvalue()


def test_what_the_last_build_checked_is_not_built_again(tmp_path):
    d = project(tmp_path)
    v, worker, judge, out = validation(d)
    edit_a_test(d)
    asyncio.run(v.after_turn(True))
    assert asyncio.run(v.at_end()) is None


def test_a_session_left_in_spec_phase_ends_in_build(tmp_path):
    d = project(tmp_path)
    phase.write(d, "spec")
    v, *_ = validation(d)
    asyncio.run(v.at_end())
    assert phase.read(d) == "build"


def test_the_spec_change_is_offered_to_the_agent_as_a_tool(tmp_path):
    d = project(tmp_path)
    v, *_ = validation(d)
    [tool] = v.extras().tools
    assert tool.name == V.CHANGE_SPEC and set(tool.params) == {"finding", "change"}
