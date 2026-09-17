"""`hamilton guard` -- the phase-gate hook backend (build-plan 2.3 / 3.1).

The four exit-criteria combinations plus payload-shape, path-normalisation and
fail-closed behaviour. `guard` is invoked exactly as the hook would invoke it:
the real console entry point with a JSON payload piped on stdin.

Payload shape targeted: the Claude Code hooks contract as documented in the
CLI itself (v2.1.x `claude` binary, "Hooks Configuration" / "Hook Input (stdin
JSON)" section):

    {"session_id": "...", "tool_name": "Write",
     "tool_input": {"file_path": "/abs/or/rel/path", "content": "..."}}

For `Edit` / `MultiEdit` the path field is likewise `tool_input.file_path`;
`NotebookEdit` uses `tool_input.notebook_path`. Extraction here tolerates both.
"""

import json
import os
import subprocess
import sys

from conftest import REPO


def run_guard(cwd, payload):
    env = {**os.environ, "PYTHONPATH": REPO}
    return subprocess.run(
        [sys.executable, "-m", "hamilton_core", "guard"],
        cwd=str(cwd), input=json.dumps(payload),
        capture_output=True, text=True, env=env,
    )


def payload(path, tool="Write"):
    field = "notebook_path" if tool == "NotebookEdit" else "file_path"
    return {"session_id": "t", "tool_name": tool,
            "tool_input": {field: path, "content": "x"}}


def make_project(root, phase="spec"):
    (root / "spec").mkdir()
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / ".hamilton").mkdir()
    (root / ".claude").mkdir()
    (root / ".hamilton" / "phase").write_text(phase)
    (root / ".hamilton" / "config").write_text("test_command=true\npaths.unit=tests\n")
    (root / ".claude" / "settings.json").write_text("{}")
    return root


# --- the four exit-criteria combinations -------------------------------------

def test_spec_phase_allows_target_inside_spec(tmp_path):
    make_project(tmp_path, "spec")
    proc = run_guard(tmp_path, payload(str(tmp_path / "spec" / "requirements.md")))
    assert proc.returncode == 0
    assert proc.stdout == "" and proc.stderr == ""


def test_spec_phase_denies_target_outside_spec(tmp_path):
    make_project(tmp_path, "spec")
    for rel in ("src/x.py", "README.md"):
        proc = run_guard(tmp_path, payload(str(tmp_path / rel)))
        assert proc.returncode != 0, rel
        assert proc.stdout == "", rel
        assert "spec" in proc.stderr and "hamilton build" in proc.stderr, proc.stderr


def test_build_phase_allows_target_outside_spec(tmp_path):
    make_project(tmp_path, "build")
    for rel in ("src/x.py", "README.md"):
        proc = run_guard(tmp_path, payload(str(tmp_path / rel)))
        assert proc.returncode == 0, (rel, proc.stderr)
        assert proc.stdout == "" and proc.stderr == ""


def test_build_phase_denies_target_inside_spec(tmp_path):
    make_project(tmp_path, "build")
    proc = run_guard(tmp_path, payload(str(tmp_path / "spec" / "requirements.md")))
    assert proc.returncode != 0
    assert "build" in proc.stderr and "hamilton design" in proc.stderr


def test_build_phase_denies_writing_the_phase_file(tmp_path):
    make_project(tmp_path, "build")
    proc = run_guard(tmp_path, payload(str(tmp_path / ".hamilton" / "phase")))
    assert proc.returncode != 0
    assert ".hamilton/phase" in proc.stderr
    assert "build" in proc.stderr and "hamilton design" in proc.stderr


def test_build_phase_denies_hamilton_state_and_claude(tmp_path):
    make_project(tmp_path, "build")
    for rel in (".hamilton/verified", ".claude/settings.json",
                ".claude/skills/hamilton/SKILL.md"):
        proc = run_guard(tmp_path, payload(str(tmp_path / rel)))
        assert proc.returncode != 0, rel
        assert "hamilton design" in proc.stderr and "build" in proc.stderr, rel


def test_build_phase_allows_hamilton_config(tmp_path):
    """Which test framework runs and where the tests live are build-time
    decisions; `.hamilton/config` holds those two keys, so it is writable in
    build (still locked in spec, where only spec/ is writable)."""
    make_project(tmp_path, "build")
    proc = run_guard(tmp_path, payload(str(tmp_path / ".hamilton" / "config")))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "" and proc.stderr == ""

    (spec := tmp_path / "s").mkdir()
    make_project(spec, "spec")
    proc = run_guard(spec, payload(str(spec / ".hamilton" / "config")))
    assert proc.returncode != 0 and "hamilton build" in proc.stderr


def test_build_phase_denies_agent_instruction_files_root_and_nested(tmp_path):
    """AGENTS.md / CLAUDE.md hold the rules; leaving them writable in build lets
    an agent edit its own constraints. Nested ones are loaded as instructions
    too, so a bare-basename match, not just the repo root."""
    make_project(tmp_path, "build")
    for rel in ("AGENTS.md", "CLAUDE.md",
                "src/CLAUDE.md", "packages/x/AGENTS.md"):
        proc = run_guard(tmp_path, payload(str(tmp_path / rel)))
        assert proc.returncode != 0, rel
        assert os.path.basename(rel) in proc.stderr, rel
        assert "hamilton design" in proc.stderr, rel


def test_build_phase_allows_other_root_and_nested_markdown(tmp_path):
    make_project(tmp_path, "build")
    for rel in ("NOTES.md", "src/README.md", "docs/AGENTS_GUIDE.md"):
        proc = run_guard(tmp_path, payload(str(tmp_path / rel)))
        assert proc.returncode == 0, (rel, proc.stderr)


def test_build_phase_allows_source_and_tests(tmp_path):
    make_project(tmp_path, "build")
    for rel in ("src/x.py", "tests/test_x.py", "README.md"):
        proc = run_guard(tmp_path, payload(str(tmp_path / rel)))
        assert proc.returncode == 0, (rel, proc.stderr)


# --- denial message content ------------------------------------------------

def test_spec_denial_names_phase_and_the_switch_command(tmp_path):
    make_project(tmp_path, "spec")
    proc = run_guard(tmp_path, payload(str(tmp_path / "src" / "x.py")))
    assert "'spec'" in proc.stderr or "phase is spec" in proc.stderr
    assert "hamilton build" in proc.stderr
    assert str(tmp_path / "src" / "x.py") in proc.stderr


def test_build_denial_names_phase_and_the_switch_command(tmp_path):
    make_project(tmp_path, "build")
    tgt = str(tmp_path / "spec" / "a.md")
    proc = run_guard(tmp_path, payload(tgt))
    assert "'build'" in proc.stderr or "phase is build" in proc.stderr
    assert "hamilton design" in proc.stderr
    assert tgt in proc.stderr


# --- not-a-Hamilton-project, non-path tools, other tools -------------------

def test_no_phase_file_allows_the_write(tmp_path):
    (tmp_path / "spec").mkdir()
    proc = run_guard(tmp_path, payload(str(tmp_path / "spec" / "x.md")))
    assert proc.returncode == 0
    assert proc.stdout == "" and proc.stderr == ""


def test_payload_without_a_file_path_is_allowed(tmp_path):
    make_project(tmp_path, "spec")
    for p in ({"session_id": "t", "tool_name": "Bash",
               "tool_input": {"command": "ls"}},
              {"session_id": "t", "tool_name": "Write", "tool_input": {}}):
        proc = run_guard(tmp_path, p)
        assert proc.returncode == 0, p
        assert proc.stderr == ""


def test_edit_tool_is_gated_like_write(tmp_path):
    make_project(tmp_path, "spec")
    proc = run_guard(tmp_path, payload(str(tmp_path / "src" / "x.py"), tool="Edit"))
    assert proc.returncode != 0
    assert "hamilton build" in proc.stderr


def test_notebook_edit_uses_notebook_path(tmp_path):
    make_project(tmp_path, "spec")
    proc = run_guard(
        tmp_path, payload(str(tmp_path / "src" / "n.ipynb"), tool="NotebookEdit"))
    assert proc.returncode != 0
    assert "hamilton build" in proc.stderr


# --- path normalisation ---------------------------------------------------

def test_relative_absolute_and_dotted_paths_classify_identically(tmp_path):
    make_project(tmp_path, "spec")
    variants = ["spec/a.md", "./spec/a.md", str(tmp_path / "spec" / "a.md")]
    codes = [run_guard(tmp_path, payload(v)).returncode for v in variants]
    assert codes == [0, 0, 0], codes

    make_project_build = tmp_path / "b"
    make_project_build.mkdir()
    make_project(make_project_build, "build")
    variants_b = ["spec/a.md", "./spec/a.md",
                  str(make_project_build / "spec" / "a.md")]
    codes_b = [run_guard(make_project_build, payload(v)).returncode
               for v in variants_b]
    assert codes_b[0] == codes_b[1] == codes_b[2] != 0, codes_b


# --- fail closed on an unreadable phase ----------------------------------

def test_unrecognised_phase_value_fails_closed(tmp_path):
    make_project(tmp_path, "banana")
    proc = run_guard(tmp_path, payload(str(tmp_path / "src" / "x.py")))
    assert proc.returncode != 0
    assert ".hamilton/phase" in proc.stderr


def test_empty_phase_file_fails_closed(tmp_path):
    make_project(tmp_path, "")
    proc = run_guard(tmp_path, payload(str(tmp_path / "spec" / "x.md")))
    assert proc.returncode != 0


# --- allow is silent, deny is non-zero with a stderr message -----------

def test_allow_is_exit_zero_and_silent(tmp_path):
    make_project(tmp_path, "spec")
    proc = run_guard(tmp_path, payload(str(tmp_path / "spec" / "x.md")))
    assert proc.returncode == 0
    assert proc.stdout == "" and proc.stderr == ""


def test_deny_is_nonzero_with_a_stderr_message_and_no_stdout(tmp_path):
    make_project(tmp_path, "spec")
    proc = run_guard(tmp_path, payload(str(tmp_path / "src" / "x.py")))
    assert proc.returncode != 0
    assert proc.stdout == ""
    assert proc.stderr.strip() != ""


# --- the phase is set by `hamilton design` / `hamilton build`, not a slash
#     command; the session driver has its own test module (test_session.py).


# --- one policy, two call sites ----------------------------------------------
#
# `guard.decide` is what both the subprocess hook backend (above) and the
# session driver's in-process permission callback consult. If they could
# disagree, a write blocked on one path would slip through on the other, so the
# two are pinned to each other here rather than tested separately.

from hamilton_core import guard as _guard  # noqa: E402

POLICY_CASES = [
    ("spec", "spec/requirements.md"),
    ("spec", "src/x.py"),
    ("spec", "README.md"),
    ("build", "src/x.py"),
    ("build", "tests/test_x.py"),
    ("build", "spec/requirements.md"),
    ("build", ".hamilton/config"),
    ("build", ".hamilton/verified"),
    ("build", ".claude/settings.json"),
    ("build", "AGENTS.md"),
    ("build", "CLAUDE.md"),
    ("banana", "src/x.py"),
]


def test_decide_agrees_with_the_hook_backend_on_every_case(tmp_path):
    for i, (phase, rel) in enumerate(POLICY_CASES):
        root = tmp_path / f"case{i}"
        root.mkdir()
        make_project(root, phase)
        target = str(root / rel)

        msg = _guard.decide(str(root), target)
        proc = run_guard(root, payload(target))

        allowed_in_process = msg is None
        allowed_by_hook = proc.returncode == 0
        assert allowed_in_process == allowed_by_hook, (phase, rel, msg, proc.returncode)
        if msg is not None:
            assert msg.strip() == proc.stderr.strip(), (phase, rel)


def test_decide_allows_when_there_is_no_phase_file(tmp_path):
    (tmp_path / "spec").mkdir()
    assert _guard.decide(str(tmp_path), str(tmp_path / "spec" / "x.md")) is None


def test_decide_resolves_a_relative_target_against_the_root(tmp_path):
    make_project(tmp_path, "spec")
    assert _guard.decide(str(tmp_path), "spec/requirements.md") is None
    assert _guard.decide(str(tmp_path), "src/x.py") is not None


def test_the_write_target_comes_from_either_path_field():
    from hamilton_core.guard import target_of
    assert target_of({"file_path": "spec/r.md"}) == "spec/r.md"
    assert target_of({"notebook_path": "n.ipynb"}) == "n.ipynb"
    assert target_of({"command": "ls"}) is None
