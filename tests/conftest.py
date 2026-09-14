"""Shared helpers for the hamilton check test-suite.

`hamilton check` writes `.hamilton/verified` on a passing run, so fixtures are
copied into a fresh temp directory before `check` runs against them -- the
committed fixture trees are never mutated.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(REPO, "tests", "fixtures")


def run_check(cwd, *args):
    """Invoke the real console entry point (`hamilton check`) in `cwd`."""
    env = {**os.environ, "PYTHONPATH": REPO}
    return subprocess.run(
        [sys.executable, "-m", "hamilton_core", "check", *args],
        cwd=str(cwd), capture_output=True, text=True, env=env,
    )


def copy_fixture(name, tmp_path):
    """Copy fixture `name` into a fresh dir under tmp_path; return its path."""
    dst = tempfile.mkdtemp(dir=str(tmp_path))
    shutil.copytree(os.path.join(FIXTURES, name), dst, dirs_exist_ok=True)
    return dst


def run_fixture(name, tmp_path, *args):
    return run_check(copy_fixture(name, tmp_path), *args)


def run_json(name, tmp_path):
    proc = run_fixture(name, tmp_path, "--json")
    return proc.returncode, json.loads(proc.stdout)
