"""Shared helpers for the hamilton verify test-suite.

Fixtures are copied into a fresh temp directory before a test touches them, so
the committed fixture trees are never mutated. Their tags carry review
suffixes as committed; a test that edits a fixture and wants it reviewed again
calls `stamp`.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(REPO, "tests", "fixtures")


def run_verify(cwd, *args):
    """Invoke the real console entry point (`hamilton verify`) in `cwd`."""
    env = {**os.environ, "PYTHONPATH": REPO}
    return subprocess.run(
        [sys.executable, "-m", "hamilton_core", "verify", *args],
        cwd=str(cwd), capture_output=True, text=True, env=env,
    )


def copy_fixture(name, tmp_path):
    """Copy fixture `name` into a fresh dir under tmp_path; return its path."""
    dst = tempfile.mkdtemp(dir=str(tmp_path))
    shutil.copytree(os.path.join(FIXTURES, name), dst, dirs_exist_ok=True)
    return dst


def stamp(root):
    """Give every counting tag under `root` its current review suffix, as a
    passed review would. Test code only: the product writes a suffix only
    when its reviewer passes the test."""
    from hamilton_core import verify, review
    spec = os.path.join(str(root), verify.REQ_REL)
    reqs, _dupes, _malformed = verify.extract(spec)
    defined = verify.extract_methods(spec)
    paths = verify.method_paths(verify.read_config(str(root)))
    tags = verify.scan(str(root), [d for ds in paths.values() for d in ds])
    for c in verify.counted(str(root), reqs, defined, paths, tags):
        review.write_suffix(str(root), c.tag, c.want)


def run_fixture(name, tmp_path, *args):
    return run_verify(copy_fixture(name, tmp_path), *args)


def run_json(name, tmp_path):
    proc = run_fixture(name, tmp_path, "--json")
    return proc.returncode, json.loads(proc.stdout)


@pytest.fixture(autouse=True)
def _brief_quiet(monkeypatch):
    """A session waits on a running or just-finished subagent before handing
    the prompt back. Tests should not sit through those waits, so they are a
    blink here; the test that pins the waiting says so itself."""
    monkeypatch.setattr("hamilton_core.session.agent.QUIET", 0.05)
    monkeypatch.setattr("hamilton_core.session.agent.STALLED", 0.05)
