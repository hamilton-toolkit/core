"""`hamilton upgrade` -- bring the framework-managed scaffold files up to date.

The managed files belong to the framework, so `upgrade` overwrites them with
the current templates without asking (and recreates any that were deleted). It
never touches the project's own files: spec/, .hamilton/phase, .hamilton/config,
.hamilton/verified.
"""

import os
import subprocess
import sys

from conftest import REPO, run_check  # noqa: F401  (run_check import mirrors siblings)

from hamilton_core.init import MANAGED, SRC_FOR, TEMPLATES

ENV = {**os.environ, "PYTHONPATH": REPO}


def _run(cwd, *args, stdin=None):
    return subprocess.run(
        [sys.executable, "-m", "hamilton_core", *args],
        cwd=str(cwd), capture_output=True, text=True, env=ENV, input=stdin,
    )


def init(cwd):
    p = _run(cwd, "init")
    assert p.returncode == 0, p.stderr
    return cwd


def template_text(rel):
    with open(os.path.join(TEMPLATES, SRC_FOR[rel]), encoding="utf-8") as fh:
        return fh.read()


def test_fresh_project_is_already_current(tmp_path):
    init(tmp_path)
    p = _run(tmp_path, "upgrade")
    assert p.returncode == 0, p.stderr
    assert "already current" in p.stdout


def test_outdated_framework_file_is_overwritten(tmp_path):
    init(tmp_path)
    rel = ".claude/settings.json"
    (tmp_path / rel).write_text("STALE OLD SCAFFOLD\n")

    p = _run(tmp_path, "upgrade")
    assert p.returncode == 0, p.stderr
    assert "updated 1 file" in p.stdout
    assert (tmp_path / rel).read_text() == template_text(rel)


def test_locally_edited_framework_file_is_overwritten_without_asking(tmp_path):
    """The skill is part of the framework: `upgrade` restores it to the current
    template with no prompt, even when the project has edited it. stdin is
    closed, so any attempt to prompt would surface as a hang or an error."""
    init(tmp_path)
    rel = ".claude/skills/hamilton/SKILL.md"
    (tmp_path / rel).write_text((tmp_path / rel).read_text() + "\n# local edit\n")

    p = _run(tmp_path, "upgrade", stdin="")
    assert p.returncode == 0, p.stderr
    assert (tmp_path / rel).read_text() == template_text(rel)
    assert "updated 1 file" in p.stdout


def test_missing_framework_file_is_recreated(tmp_path):
    init(tmp_path)
    rel = ".claude/skills/hamilton/SKILL.md"
    (tmp_path / rel).unlink()

    p = _run(tmp_path, "upgrade")
    assert p.returncode == 0, p.stderr
    assert (tmp_path / rel).read_text() == template_text(rel)


def test_upgrade_never_touches_spec_or_hamilton_state(tmp_path):
    init(tmp_path)
    protected = {
        "spec/requirements.md": "SENTINEL requirements\n",
        "spec/actors.md": "SENTINEL actors\n",
        ".hamilton/config": "test_command=SENTINEL\npaths.unit=tests\n",
        ".hamilton/phase": "build",
        ".hamilton/verified": "R-0001/AC1 sha256:deadbeef\n",
    }
    for rel, body in protected.items():
        (tmp_path / rel).write_text(body)
    (tmp_path / ".claude/skills/hamilton/SKILL.md").write_text("old\n")  # real work to do

    p = _run(tmp_path, "upgrade")
    assert p.returncode == 0, p.stderr
    for rel, body in protected.items():
        assert (tmp_path / rel).read_text() == body, f"{rel} was modified"
    assert "=== spec/" not in p.stdout
    assert "a/spec/" not in p.stdout


def test_no_gitlab_ci_is_scaffolded_or_managed(tmp_path):
    """The CI pipeline feature is gone: nothing named .gitlab-ci.yml is written
    by init, listed in MANAGED, or created by upgrade -- and it is NOT retired
    either, because by now a project's pipeline may be a real one."""
    from hamilton_core.init import RETIRED
    assert ".gitlab-ci.yml" not in MANAGED and ".gitlab-ci.yml" not in RETIRED
    init(tmp_path)
    (tmp_path / ".gitlab-ci.yml").write_text("my own pipeline\n")

    p = _run(tmp_path, "upgrade")
    assert p.returncode == 0, p.stderr
    assert (tmp_path / ".gitlab-ci.yml").read_text() == "my own pipeline\n"
    assert "gitlab-ci" not in p.stdout


def test_diff_is_shown_for_each_change(tmp_path):
    init(tmp_path)
    (tmp_path / ".claude/settings.json").write_text("totally different\n")

    p = _run(tmp_path, "upgrade")
    assert "=== .claude/settings.json ===" in p.stdout
    assert "--- a/.claude/settings.json" in p.stdout
    assert "+++ b/.claude/settings.json" in p.stdout
    assert "-totally different" in p.stdout


def test_upgrade_is_idempotent(tmp_path):
    init(tmp_path)
    (tmp_path / ".claude/settings.json").write_text("old\n")

    first = _run(tmp_path, "upgrade")
    assert "updated 1 file" in first.stdout
    second = _run(tmp_path, "upgrade")
    assert "already current" in second.stdout


def test_retired_scaffold_files_are_deleted(tmp_path):
    """An older project still has `.claude/commands/`; `upgrade` removes it."""
    d = init(tmp_path)
    cmd = d / ".claude" / "commands"
    cmd.mkdir(parents=True)
    (cmd / "spec.md").write_text("old /spec command\n")
    (cmd / "build.md").write_text("old /build command\n")

    p = _run(d, "upgrade")
    assert p.returncode == 0, p.stderr
    assert "retired -- deleted" in p.stdout
    assert not cmd.exists()                       # empty dir pruned too

    assert "already current" in _run(d, "upgrade").stdout


def test_missing_template_is_a_clear_error_not_a_traceback(tmp_path, monkeypatch):
    """A broken install (a MANAGED template absent from the package) fails with
    a reinstall message, not None.splitlines()."""
    from hamilton_core import upgrade
    d = init(tmp_path)
    empty = tmp_path / "no-templates"
    empty.mkdir()
    monkeypatch.setattr(upgrade, "TEMPLATES", str(empty))
    import io
    import contextlib
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = upgrade.main(str(d))
    assert rc == 1
    assert "package is broken" in err.getvalue() and "Reinstall" in err.getvalue()


def test_upgrade_outside_a_project_exits_1(tmp_path):
    p = _run(tmp_path, "upgrade")
    assert p.returncode == 1
    assert "not a Hamilton project" in p.stderr
