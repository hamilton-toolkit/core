"""`hamilton design` / `hamilton build` -- the phase-scoping launcher.

`design`/`build` set `.hamilton/phase`, print a status banner, then run the
`agent_command` as a **child process** and wait. The tests point
`agent_command` at a short python snippet that records what it inherited, then
returns.
"""

import json
import os
import subprocess
import sys

from conftest import REPO

ENV = {**os.environ, "PYTHONPATH": REPO}
PY = sys.executable


def run(cwd, cmd, env=None):
    return subprocess.run([PY, "-m", "hamilton_core", cmd], cwd=str(cwd),
                          capture_output=True, text=True, env=env or ENV)


def project(tmp_path, agent_command="", phase="spec", requirements=None):
    d = tmp_path / "proj"
    (d / ".hamilton").mkdir(parents=True)
    (d / ".hamilton" / "phase").write_text(phase)
    (d / ".hamilton" / "config").write_text(
        f"agent_command={agent_command}\ntest_command=true\ntest_paths=tests\n")
    if requirements is not None:
        (d / "spec").mkdir()
        (d / "spec" / "requirements.md").write_text(requirements)
    return d


LIVE_REQ = (
    "## R-0001 A derived thing\n"
    "Statement: The thing produces an observable result.\n"
    "Criteria:\n"
    "- AC1: an input -> the expected output\n"
)


# a snippet that dumps what the agent inherited (env, phase file, its own
# argv -- the launcher appends a kickoff prompt as the final argument), then
# exits 0
RECORD = (f'{PY} -c "import os,json,sys,pathlib; '
          f"pathlib.Path('inherited.json').write_text(json.dumps({{"
          f"'session': os.environ.get('HAMILTON_SESSION'), "
          f"'phase': open('.hamilton/phase').read(), "
          f"'argv': sys.argv}}))\"")


def test_no_hamilton_dir_exits_2(tmp_path):
    (tmp_path / "proj").mkdir()
    p = run(tmp_path / "proj", "design")
    assert p.returncode == 2 and "no .hamilton/" in p.stderr


def test_agent_command_unset_exits_1(tmp_path):
    p = run(project(tmp_path, agent_command=""), "design")
    assert p.returncode == 1 and "agent_command is not set" in p.stderr


def test_design_sets_spec_phase_and_runs_the_agent(tmp_path):
    d = project(tmp_path, RECORD, phase="build")
    p = run(d, "design")
    assert p.returncode == 0, p.stderr
    assert "Hamilton · spec phase" in p.stderr           # banner
    assert "phase is 'spec'; launching" in p.stderr
    assert "session ended (phase 'spec')" in p.stderr         # footer
    got = json.loads((d / "inherited.json").read_text())
    assert got["session"] == "spec"
    assert got["phase"] == "spec"
    assert "modify/extend existing requirements" in got["argv"][-1]
    assert (d / ".hamilton" / "phase").read_text() == "spec"


def test_build_sets_build_phase_and_runs_the_agent(tmp_path):
    d = project(tmp_path, RECORD, phase="spec")
    p = run(d, "build")
    assert p.returncode == 0, p.stderr
    assert "Hamilton · build phase" in p.stderr
    assert "session ended (phase 'build')" in p.stderr
    got = json.loads((d / "inherited.json").read_text())
    assert got["session"] == "build" and got["phase"] == "build"
    assert "Propagate a change" in got["argv"][-1]


def test_an_agent_inside_a_session_cannot_switch_phase(tmp_path):
    """agent_command shells back to `hamilton build` (a wrapper that drops the
    appended kickoff arg); it inherits HAMILTON_SESSION=spec and is refused.
    The phase stays 'spec'."""
    inner = (f'{PY} -c "import subprocess,sys; '
             f"sys.exit(subprocess.run([sys.executable,'-m','hamilton_core',"
             f"'build']).returncode)\"")
    d = project(tmp_path, inner, phase="spec")
    p = run(d, "design")
    assert p.returncode == 1
    assert "already inside a Hamilton session" in p.stderr
    assert "HAMILTON_SESSION='spec'" in p.stderr
    assert (d / ".hamilton" / "phase").read_text() == "spec"


def test_session_env_already_set_is_refused(tmp_path):
    d = project(tmp_path, RECORD)
    p = run(d, "design", env={**ENV, "HAMILTON_SESSION": "build"})
    assert p.returncode == 1
    assert "already inside" in p.stderr and not (d / "inherited.json").exists()


def test_unlaunchable_agent_command_exits_1(tmp_path):
    d = project(tmp_path, "definitely-not-a-real-binary-xyz")
    p = run(d, "build")
    assert p.returncode == 1 and "could not launch" in p.stderr
    # phase was still set before the failed launch -- documented behaviour
    assert (d / ".hamilton" / "phase").read_text() == "build"


def test_reverse_sets_spec_phase_and_runs_the_agent(tmp_path):
    """`hamilton reverse` is a spec-phase launcher with the brownfield kickoff.
    An empty spec (no requirements.md) passes the non-empty-spec guard."""
    d = project(tmp_path, RECORD, phase="build")
    p = run(d, "reverse")
    assert p.returncode == 0, p.stderr
    assert "Hamilton · spec phase" in p.stderr                # banner
    assert "phase is 'spec'; launching" in p.stderr
    assert "hamilton reverse: session ended (phase 'spec')" in p.stderr  # footer
    got = json.loads((d / "inherited.json").read_text())
    assert got["session"] == "spec"
    assert got["phase"] == "spec"
    assert "Reverse-engineer the spec from existing code" in got["argv"][-1]
    assert (d / ".hamilton" / "phase").read_text() == "spec"


def test_reverse_refused_when_spec_already_has_requirements(tmp_path):
    """`hamilton reverse` derives a *first* spec; it will not run against a spec
    that already has real requirements -- it points at `hamilton design`."""
    d = project(tmp_path, RECORD, requirements=LIVE_REQ)
    p = run(d, "reverse")
    assert p.returncode == 1
    assert "already has 1 requirement(s)" in p.stderr
    assert "hamilton design" in p.stderr
    assert not (d / "inherited.json").exists()               # agent never ran


def test_reverse_runs_when_requirements_file_holds_only_the_example(tmp_path):
    """A fenced example is not a real requirement, so the guard lets it run."""
    fenced = "# Requirements\n\n```markdown\n## R-0001 Example\nStatement: x.\n" \
             "Criteria:\n- AC1: a -> b\n```\n"
    d = project(tmp_path, RECORD, requirements=fenced)
    p = run(d, "reverse")
    assert p.returncode == 0, p.stderr
    assert (d / "inherited.json").exists()


def test_reverse_inside_a_session_is_refused(tmp_path):
    d = project(tmp_path, RECORD)
    p = run(d, "reverse", env={**ENV, "HAMILTON_SESSION": "spec"})
    assert p.returncode == 1
    assert "already inside" in p.stderr and not (d / "inherited.json").exists()
