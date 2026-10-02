"""`hamilton run` -- starts `start_command` for the engineer, in the
foreground, from the project root."""

import os
import subprocess
import sys

from conftest import REPO, copy_fixture


def run_hamilton_run(cwd):
    env = {**os.environ, "PYTHONPATH": REPO}
    return subprocess.run([sys.executable, "-m", "hamilton_core", "run"],
                          cwd=str(cwd), capture_output=True, text=True, env=env)


def test_without_a_start_command_it_says_how_to_get_one(tmp_path):
    d = copy_fixture("clean", tmp_path)
    proc = run_hamilton_run(d)
    assert proc.returncode == 2
    assert "no start_command" in proc.stderr
    assert "hamilton build" in proc.stderr


def test_it_runs_the_command_in_the_project_root(tmp_path):
    d = copy_fixture("clean", tmp_path)
    with open(f"{d}/.hamilton/config", "a") as fh:
        fh.write("start_command=pwd && echo started\n")
    proc = run_hamilton_run(d)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == [os.path.realpath(d), "started"]


def test_it_exits_with_the_commands_own_code(tmp_path):
    d = copy_fixture("clean", tmp_path)
    with open(f"{d}/.hamilton/config", "a") as fh:
        fh.write("start_command=exit 3\n")
    assert run_hamilton_run(d).returncode == 3


def test_outside_a_project_it_refuses(tmp_path):
    proc = run_hamilton_run(tmp_path)
    assert proc.returncode == 2
    assert ".hamilton/config" in proc.stderr
