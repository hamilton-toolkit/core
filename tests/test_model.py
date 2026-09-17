"""`hamilton_core.model` -- the shared spec reader behind the view commands.

One requirement tree plus a flat actor list (D-014). Unit-level checks on
parsing and the derived views; the commands' own tests cover rendering.
"""

import os

from conftest import copy_fixture

from hamilton_core import model as M
from hamilton_core.check import extract_methods


def load(name, tmp_path):
    return M.Model(copy_fixture(name, tmp_path))


def test_parses_requirements_and_actors(tmp_path):
    m = load("model", tmp_path)
    assert set(m.reqs) == {"R-0100", "R-0001", "R-0004", "R-0007"}
    assert m.reqs["R-0001"]["parent"] == "R-0100"
    assert m.reqs["R-0100"]["parent"] is None
    assert m.actors["A-0001"]["name"] == "End User"


def test_methods_are_parsed_per_criterion_and_defined_once(tmp_path):
    m = load("model", tmp_path)
    assert m.reqs["R-0001"]["acs"]["AC1"]["methods"] == ["http"]
    assert m.reqs["R-0004"]["acs"]["AC2"]["methods"] == ["unit"]
    path = os.path.join(m.root, "spec", "requirements.md")
    assert set(extract_methods(path)) == {"http", "unit"}
    assert m.paths == {"http": ["tests"], "unit": ["tests"]}


def test_criterion_text_keeps_its_marker(tmp_path):
    m = load("model", tmp_path)
    assert m.reqs["R-0001"]["acs"]["AC1"]["text"].endswith("[http]")


def test_retired_interface_field_is_dropped_not_stored(tmp_path):
    d = copy_fixture("model", tmp_path)
    path = os.path.join(d, "spec", "requirements.md")
    body = open(path).read().replace(
        "## R-0001 Create a customer\nParent: R-0100\n",
        "## R-0001 Create a customer\nParent: R-0100\nInterface: POST /customers\n")
    open(path, "w").write(body)
    m = M.Model(d)
    assert "interface" not in m.reqs["R-0001"]
    assert not m.malformed


def test_actor_field_is_captured_on_requirements(tmp_path):
    m = load("model", tmp_path)
    assert m.reqs["R-0100"]["actor"] == "A-0001"
    assert m.reqs["R-0001"]["actor"] is None


def test_retired_component_field_is_dropped_not_stored(tmp_path):
    d = copy_fixture("model", tmp_path)
    path = os.path.join(d, "spec", "requirements.md")
    body = open(path).read().replace(
        "## R-0004 Reject an invalid name\nParent: R-0001\n",
        "## R-0004 Reject an invalid name\nParent: R-0001\nComponent: C-0009\n")
    open(path, "w").write(body)
    m = M.Model(d)
    assert "component" not in m.reqs["R-0004"]
    assert m.reqs["R-0004"]["parent"] == "R-0001"


def test_parsers_skip_the_fenced_example_in_each_file(tmp_path):
    m = load("model", tmp_path)
    assert "A-9999" not in m.actors
    assert "R-9999" not in m.reqs


def test_missing_actor_file_parses_as_empty(tmp_path):
    d = tmp_path / "bare"
    (d / "spec").mkdir(parents=True)
    (d / "spec" / "requirements.md").write_text(
        "# Requirements\n\n## R-0001 X\nActor: A-0001\n"
        "Statement: x.\nCriteria:\n- AC1: a -> b\n")
    m = M.Model(str(d))
    assert m.actors == {}
    assert set(m.reqs) == {"R-0001"}


def test_dotted_paths_number_roots_then_children(tmp_path):
    m = load("model", tmp_path)
    paths = M.dotted_paths({k: r["parent"] for k, r in m.reqs.items()})
    assert paths == {"R-0100": "1", "R-0001": "1.1", "R-0004": "1.1.1",
                     "R-0007": "1.2"}


def test_dotted_paths_break_a_cycle_instead_of_recursing(tmp_path):
    # a -> b -> a : both become roots rather than looping forever
    paths = M.dotted_paths({"R-0001": "R-0002", "R-0002": "R-0001"})
    assert set(paths) == {"R-0001", "R-0002"}


def test_dotted_paths_treat_unresolved_parent_as_root(tmp_path):
    paths = M.dotted_paths({"R-0005": "R-9000"})
    assert paths == {"R-0005": "1"}


def test_path_key_orders_naturally(tmp_path):
    assert sorted(["1.10", "1.2", "2", "10"], key=M.path_key) == \
        ["1.2", "1.10", "2", "10"]


def test_requirement_status_rollup(tmp_path):
    m = load("model", tmp_path)
    assert m.req_status("R-0001") == "covered"
    assert m.req_status("R-0004") == "uncovered"
    assert m.req_status("R-0007") == "stale"


def test_criterion_status_follows_its_method(tmp_path):
    d = copy_fixture("model", tmp_path)
    path = os.path.join(d, "spec", "requirements.md")
    body = open(path).read().replace(
        "- AC1: valid payload -> 201 and a customer id [http]",
        "- AC1: valid payload -> 201 and a customer id [browser]").replace(
        "- AC1: the shop is reachable -> the home page renders [http]",
        "- AC1: the shop is reachable -> the home page renders")
    open(path, "w").write(
        body + "\n## R-0200 Looks calm\nActor: A-0001\nStatement: The shop looks calm.\n"
        "Criteria:\n- AC1: a visitor looks -> it feels calm [manual]\n")
    m = M.Model(d)
    assert m.ac_status("R-0001", "AC1") == "uncovered"      # tagged, not under browser
    assert m.ac_status("R-0100", "AC1") == "no method"
    assert m.ac_status("R-0200", "AC1") == "manual"
    assert m.req_status("R-0200") == "manual"


def test_status_unknown_without_config(tmp_path):
    d = copy_fixture("model", tmp_path)
    os.remove(os.path.join(d, ".hamilton", "config"))
    m = M.Model(d)
    assert not m.coverage_known
    assert m.req_status("R-0001") == "unknown"


def test_reverse_links(tmp_path):
    m = load("model", tmp_path)
    assert m.child_requirements("R-0100") == ["R-0001", "R-0007"]
    assert m.child_requirements("R-0001") == ["R-0004"]
    assert m.requirements_for_actor("A-0001") == ["R-0100"]
