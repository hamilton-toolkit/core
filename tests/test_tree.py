"""`hamilton tree` -- the one requirement tree (D-014).

`model` fixture: root R-0100 with children R-0001 and R-0007; R-0004 a child of
R-0001. R-0100 and R-0001 are interior nodes and carry an `Interface:`.
Coverage rolls the requirements up to covered / uncovered / stale.
"""

import json
import os
import subprocess
import sys

from conftest import REPO, copy_fixture


def run_tree(cwd, *args):
    env = {**os.environ, "PYTHONPATH": REPO}
    return subprocess.run(
        [sys.executable, "-m", "hamilton_core", "tree", *args],
        cwd=str(cwd), capture_output=True, text=True, env=env,
    )


def tree(name, tmp_path, *args):
    return run_tree(copy_fixture(name, tmp_path), *args)


def test_tree_has_computed_dotted_paths_and_ids(tmp_path):
    out = tree("model", tmp_path).stdout
    lines = [l for l in out.splitlines() if l.strip()]
    assert lines[0].startswith("1  ") and "(R-0100)" in lines[0]     # root
    assert lines[1].startswith("1.1  ") and "(R-0001)" in lines[1]
    assert lines[2].startswith("1.1.1") and "(R-0004)" in lines[2]
    assert lines[3].startswith("1.2  ") and "(R-0007)" in lines[3]


def test_rows_show_the_one_line_statement(tmp_path):
    out = tree("model", tmp_path).stdout
    assert "A customer is created from a valid payload." in out
    assert "Customer creation rejects an invalid name." in out


def test_interior_node_marker_reflects_the_interface(tmp_path):
    """`i` = interior with an Interface:, `!` = interior still missing one."""
    d = copy_fixture("model", tmp_path)
    rows = {l.split("(")[1].split(")")[0]: l
            for l in run_tree(d).stdout.splitlines() if "(R-" in l}
    assert " i (R-0100" in rows["R-0100"]
    assert " i (R-0001" in rows["R-0001"]
    assert " i (" not in rows["R-0004"] and " ! (" not in rows["R-0004"]
    # drop R-0001's Interface -> its marker flips to `!`
    path = os.path.join(d, "spec", "requirements.md")
    body = open(path).read().replace(
        "Interface: POST /customers taking a JSON payload, returning 201 and an id.\n", "")
    open(path, "w").write(body)
    rows = {l.split("(")[1].split(")")[0]: l
            for l in run_tree(d).stdout.splitlines() if "(R-" in l}
    assert " ! (R-0001" in rows["R-0001"]


def test_coverage_status_marked_per_requirement(tmp_path):
    rows = {l.split("(")[1].split(")")[0]: l
            for l in tree("model", tmp_path).stdout.splitlines()
            if "(R-" in l}
    assert "[covered]" in rows["R-0001"]
    assert "[uncovered]" in rows["R-0004"]
    assert "[stale]" in rows["R-0007"]


def test_dotted_paths_are_not_stored_anywhere(tmp_path):
    d = copy_fixture("model", tmp_path)
    run_tree(d)
    body = open(os.path.join(d, "spec", "requirements.md")).read()
    assert "1.1.1" not in body


def test_json_rows(tmp_path):
    data = json.loads(tree("model", tmp_path, "--json").stdout)
    by_id = {r["id"]: r for r in data["rows"]}
    assert by_id["R-0004"]["path"] == "1.1.1"
    assert by_id["R-0004"]["parent"] == "R-0001"
    assert by_id["R-0100"]["path"] == "1" and by_id["R-0100"]["parent"] is None
    assert by_id["R-0100"]["actor"] == "A-0001"
    assert by_id["R-0100"]["boundary"] is True
    assert by_id["R-0001"]["interface"].startswith("POST /customers")
    assert by_id["R-0004"]["boundary"] is False
    assert by_id["R-0007"]["status"] == "stale"
    assert by_id["R-0001"]["criteria"] == {"AC1": "covered"}
    assert "_text" not in by_id["R-0001"]


def test_natural_order_beyond_nine(tmp_path):
    d = copy_fixture("model", tmp_path)
    extra = "".join(
        f"\n## R-01{n:02d} Extra {n}\nParent: R-0001\n"
        f"Statement: extra requirement {n}.\nCriteria:\n- AC1: x -> y\n"
        for n in range(2, 11))
    open(os.path.join(d, "spec", "requirements.md"), "a").write(extra)
    rows = [l for l in run_tree(d).stdout.splitlines()
            if "(R-01" in l and "(R-0100)" not in l]
    paths = [l.split()[0] for l in rows]
    assert paths == sorted(paths, key=lambda p: [int(x) for x in p.split(".")])
    assert "1.1.10" in paths and paths.index("1.1.2") < paths.index("1.1.10")


def test_outside_a_project_exits_2(tmp_path):
    p = run_tree(tmp_path)
    assert p.returncode == 2
    assert "not found" in p.stderr
