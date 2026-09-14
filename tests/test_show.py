"""`hamilton show <ID>` -- the per-entity view. Two id types (D-014):
`R-nnnn` requirement, `A-nnnn` actor.

`tree` fixture: a 3-level requirement Parent chain (R-0001 -> R-0007 -> R-0042),
one covered-but-stale AC, one uncovered AC. `model` fixture: a fuller tree with
an interior node that carries an `Interface:` and an actor on the root.
Fixtures are copied to a temp dir first (conftest) since a stray `check` may
write verified.
"""

import os
import re
import subprocess
import sys

from conftest import REPO, copy_fixture


def run_show(cwd, *args):
    env = {**os.environ, "PYTHONPATH": REPO}
    return subprocess.run(
        [sys.executable, "-m", "hamilton_core", "show", *args],
        cwd=str(cwd), capture_output=True, text=True, env=env,
    )


def show(name, tmp_path, *args):
    return run_show(copy_fixture(name, tmp_path), *args)


# -- requirement view ----------------------------------------------------- #

def test_shows_title_statement_and_all_criteria(tmp_path):
    p = show("tree", tmp_path, "R-0042")
    assert p.returncode == 0, p.stderr
    out = p.stdout
    assert 'R-0042 "Reject expired tokens"' in out
    assert "The auth middleware rejects a request whose token exp claim is in the past." in out
    assert "expired token -> 401 and no user data in the response body" in out
    assert "token inside the 30s clock-skew window -> accepted" in out


def test_renders_the_parent_path_by_title_not_by_id(tmp_path):
    out = show("tree", tmp_path, "R-0042").stdout
    assert "Authentication › Sessions › Reject expired tokens" in out


def test_never_prints_a_bare_requirement_id(tmp_path):
    out = show("tree", tmp_path, "R-0042").stdout
    for m in re.finditer(r"R-\d{4}", out):
        assert out[m.end():m.end() + 2] == ' "', f"bare id near {out[m.start():m.start()+40]!r}"


def test_interior_node_shows_its_interface(tmp_path):
    out = show("model", tmp_path, "R-0001").stdout
    assert "interface:" in out
    assert "POST /customers taking a JSON payload" in out


def test_root_shows_its_actor(tmp_path):
    out = show("model", tmp_path, "R-0100").stdout
    assert 'A-0001 "End User"' in out


def test_leaf_shows_no_actor_or_interface_line(tmp_path):
    out = show("model", tmp_path, "R-0004").stdout
    assert "interface:" not in out
    assert "actor:" not in out


def test_criterion_coverage_status_and_tag_location(tmp_path):
    out = show("model", tmp_path, "R-0007").stdout
    # R-0007/AC1 is tagged in tests/covers.js and its verified hash is stale
    block = out.split("AC1", 1)[1]
    assert "[stale]" in block
    assert "tests/covers.js:" in block


def test_uncovered_criterion_says_no_tag(tmp_path):
    out = show("model", tmp_path, "R-0004").stdout
    assert "[uncovered]" in out
    assert "no @covers tag" in out


def test_requirement_lists_its_child_requirements(tmp_path):
    out = show("model", tmp_path, "R-0001").stdout
    assert "children:" in out
    assert 'R-0004 "Reject an invalid name"' in out.split("children:", 1)[1]


def test_coverage_unknown_without_config(tmp_path):
    d = copy_fixture("tree", tmp_path)
    os.remove(os.path.join(d, ".hamilton", "config"))
    p = run_show(d, "R-0042")
    assert p.returncode == 0
    assert "coverage unknown" in p.stdout


def test_show_renders_a_root_missing_its_actor_without_crashing(tmp_path):
    """A parent-less requirement with no `Actor:` is an `orphan-requirement`
    finding, but the read-only view must still render it."""
    d = copy_fixture("tree", tmp_path)
    body = open(os.path.join(d, "spec", "requirements.md")).read().replace(
        "## R-0001 Authentication\nActor: A-0001\n",
        "## R-0001 Authentication\n")
    open(os.path.join(d, "spec", "requirements.md"), "w").write(body)
    p = run_show(d, "R-0001")
    assert p.returncode == 0
    assert "must name one" in p.stdout


# -- actor view --------------------------------------------------------- #

def test_actor_view_lists_its_requirements(tmp_path):
    out = show("model", tmp_path, "A-0001").stdout
    assert 'A-0001 "End User"' in out
    assert "Places orders and manages their account through the web UI." in out
    assert 'R-0100 "Run the shop"' in out.split("named by requirements:", 1)[1]


def test_actor_lookup_ignores_the_fenced_example(tmp_path):
    p = run_show(copy_fixture("model", tmp_path), "A-9999")
    assert p.returncode == 2
    assert "not declared" in p.stderr


# -- json --------------------------------------------------------------- #

def test_json_requirement_has_criteria_with_status_and_tags(tmp_path):
    import json
    p = show("model", tmp_path, "R-0007", "--json")
    assert p.returncode == 0, p.stderr
    data = json.loads(p.stdout)
    assert data["type"] == "requirement" and data["id"] == "R-0007"
    ac1 = next(c for c in data["criteria"] if c["id"] == "AC1")
    assert ac1["status"] == "stale"
    assert ac1["tags"] and ac1["tags"][0]["file"] == "tests/covers.js"


def test_json_requirement_carries_interface_and_boundary(tmp_path):
    import json
    data = json.loads(show("model", tmp_path, "R-0001", "--json").stdout)
    assert data["boundary"] is True
    assert data["interface"].startswith("POST /customers")
    assert data["children"][0]["id"] == "R-0004"


def test_json_actor_has_its_requirements(tmp_path):
    import json
    data = json.loads(show("model", tmp_path, "A-0001", "--json").stdout)
    assert data["type"] == "actor"
    assert data["requirements"] == ["R-0100"]


# -- errors ----------------------------------------------------------- #

def test_unknown_id_exits_2(tmp_path):
    p = show("tree", tmp_path, "R-9999")
    assert p.returncode == 2
    assert "not declared" in p.stderr


def test_missing_id_argument_exits_2(tmp_path):
    p = show("tree", tmp_path)
    assert p.returncode == 2
    assert "entity id" in p.stderr


def test_bad_id_shape_exits_2(tmp_path):
    p = show("model", tmp_path, "R-1")
    assert p.returncode == 2


def test_component_id_is_rejected_now(tmp_path):
    """`C-`/`I-`/`M-` ids are gone from the model (D-014); `show` treats them
    as a bad id shape."""
    p = show("model", tmp_path, "C-0001")
    assert p.returncode == 2


def test_outside_a_project_exits_2(tmp_path):
    p = run_show(tmp_path, "R-0001")
    assert p.returncode == 2
    assert "not found" in p.stderr
