"""`hamilton status` / the launcher banner -- `hamilton_core/status.py`.

`render(root, phase)` is a pure snapshot: it parses the spec and scans for
`@covers` tags but never runs the suite and never writes.
"""

import os
import subprocess
import sys

from conftest import REPO, copy_fixture

from hamilton_core import status

PY = sys.executable
ENV = {**os.environ, "PYTHONPATH": REPO}


def _git(d, *args):
    subprocess.run(["git", "-C", str(d), "-c", "user.email=t@t",
                    "-c", "user.name=t", *args],
                   check=True, capture_output=True)


def test_render_reports_counts_and_coverage(tmp_path):
    root = copy_fixture("tree", tmp_path)
    out = status.render(root, "spec")
    assert "Hamilton · spec phase" in out
    # tree fixture: 3 requirements, 4 ACs; R-0042/AC1 tagged but its recorded
    # hash is stale -> 0 covered, 3 uncovered, 1 stale
    assert "3 requirement(s), 4 acceptance criteria" in out
    assert "0/4 covered" in out and "3 uncovered" in out and "1 stale" in out
    assert "gate last passed" in out


def test_render_coverage_unknown_without_config(tmp_path):
    root = copy_fixture("tree", tmp_path)
    os.remove(os.path.join(root, ".hamilton", "config"))
    out = status.render(root, "spec")
    assert "coverage unknown" in out


def test_render_lists_last_three_spec_changes(tmp_path):
    root = copy_fixture("tree", tmp_path)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "c1")
    req = os.path.join(root, "spec", "requirements.md")
    for n in range(2, 5):
        with open(req, "a", encoding="utf-8") as fh:
            fh.write(f"\n<!-- edit {n} -->\n")
        _git(root, "commit", "-qam", f"spec change {n}")
    out = status.render(root, "spec")
    assert "spec change 4" in out and "spec change 3" in out
    assert "spec change 2" in out
    assert "c1" not in out                       # only the last three


def test_render_no_git_history(tmp_path):
    root = copy_fixture("tree", tmp_path)      # a plain dir, not a repo
    out = status.render(root, "spec")
    assert "(no git history for spec/)" in out


def test_build_banner_shows_uncommitted_spec_only_in_build(tmp_path):
    root = copy_fixture("tree", tmp_path)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    with open(os.path.join(root, "spec", "requirements.md"), "a",
              encoding="utf-8") as fh:
        fh.write("\n<!-- pending edit -->\n")

    build = status.render(root, "build")
    assert "Uncommitted spec/ changes to propagate:" in build
    assert "requirements.md" in build.split("propagate:")[1]

    spec = status.render(root, "spec")
    assert "Uncommitted spec/ changes to propagate:" not in spec


def test_status_command_outside_project_exits_2(tmp_path):
    p = subprocess.run([PY, "-m", "hamilton_core", "status"], cwd=str(tmp_path),
                       capture_output=True, text=True, env=ENV)
    assert p.returncode == 2 and "spec/ not found" in p.stderr


def test_status_command_prints_banner(tmp_path):
    root = copy_fixture("tree", tmp_path)
    p = subprocess.run([PY, "-m", "hamilton_core", "status"], cwd=root,
                       capture_output=True, text=True, env=ENV)
    assert p.returncode == 0, p.stderr
    assert "Hamilton ·" in p.stdout
    assert "3 requirement(s), 4 acceptance criteria" in p.stdout
