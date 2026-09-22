"""`hamilton show` with no id -- the one requirement tree (D-014), printed when
there is no terminal and walked when there is.

`model` fixture: root R-0100 with children R-0001 and R-0007; R-0004 a child of
R-0001.
Coverage rolls the requirements up to covered / uncovered / unreviewed.
"""

import json
import os
import subprocess
import sys
import threading
import time

from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from conftest import REPO, copy_fixture
from hamilton_core.session import widgets


def run_tree(cwd, *args):
    env = {**os.environ, "PYTHONPATH": REPO}
    return subprocess.run(
        [sys.executable, "-m", "hamilton_core", "show", *args],
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


def test_rows_carry_no_interface_marker_or_legend(tmp_path):
    out = tree("model", tmp_path).stdout
    assert "Interface" not in out
    assert all(l.split()[1].startswith("(R-") for l in out.splitlines() if l.strip())


def test_coverage_status_marked_per_requirement(tmp_path):
    rows = {l.split("(")[1].split(")")[0]: l
            for l in tree("model", tmp_path).stdout.splitlines()
            if "(R-" in l}
    assert "[covered]" in rows["R-0001"]
    assert "[uncovered]" in rows["R-0004"]
    assert "[unreviewed]" in rows["R-0007"]


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
    assert "interface" not in by_id["R-0001"] and "boundary" not in by_id["R-0001"]
    assert by_id["R-0007"]["status"] == "unreviewed"
    assert by_id["R-0001"]["criteria"] == {"AC1": "covered"}
    assert by_id["R-0004"]["label"].startswith('R-0004 "')
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


def test_rows_are_what_the_json_prints(tmp_path):
    from hamilton_core import show as S
    d = copy_fixture("model", tmp_path)
    printed = json.loads(run_tree(d, "--json").stdout)["rows"]
    public = [{k: v for k, v in r.items() if not k.startswith("_")}
              for r in S.rows(d)]
    assert public == printed


def test_an_empty_spec_says_so(tmp_path):
    d = copy_fixture("model", tmp_path)
    open(os.path.join(d, "spec", "requirements.md"), "w").write("# Requirements\n")
    p = run_tree(d)
    assert p.returncode == 0
    assert "declares no requirements" in p.stdout


# -- walking the tree on a terminal ---------------------------------------- #

UP, DOWN, RIGHT, LEFT = "\x1b[A", "\x1b[B", "\x1b[C", "\x1b[D"

# the `model` fixture's shape: R-0100 > (R-0001 > R-0004), R-0007
MODEL = [(0, "R-0100", ["root view"]), (1, "R-0001", ["one view"]),
         (2, "R-0004", ["four view"]), (1, "R-0007", ["seven view"])]


def walk(*keys):
    """Run the tree browser on MODEL, send the keys and leave with `q`.
    The result is the index selected when it was left."""
    with create_pipe_input() as pipe:
        threading.Thread(target=lambda: (time.sleep(0.05),
                                         pipe.send_text("".join(keys) + "q"))).start()
        with create_app_session(input=pipe, output=DummyOutput()):
            return widgets.tree(MODEL).run()


def test_down_moves_through_the_open_tree():
    assert walk(DOWN, DOWN, DOWN) == 3


def test_movement_stops_at_either_end():
    assert walk(UP) == 0
    assert walk(DOWN, DOWN, DOWN, DOWN) == 3


def test_left_folds_so_down_skips_the_children():
    assert walk(DOWN, LEFT, DOWN) == 3


def test_right_unfolds_a_folded_item():
    assert walk(DOWN, LEFT, RIGHT, DOWN) == 2


def test_right_on_an_open_item_steps_into_its_first_child():
    assert walk(DOWN, RIGHT) == 2


def test_left_on_a_leaf_goes_to_its_parent():
    assert walk(DOWN, DOWN, LEFT) == 1


def test_folding_the_root_hides_everything_under_it():
    assert walk(LEFT, DOWN) == 0


def test_enter_opens_the_details_where_up_and_down_scroll_instead_of_moving():
    # Enter opens, the arrows scroll, `q` goes back; the final `q` leaves
    assert walk(DOWN, "\r", DOWN, DOWN, "q") == 1
