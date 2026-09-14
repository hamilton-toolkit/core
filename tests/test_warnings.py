"""`hamilton check` advisory warnings: `long-statement`, `long-description`
and `no-interface`.

Warnings never change the exit code and never turn an existing project red --
they only flag spec prose that has drifted from the authoring rules. The
`warnings` fixture has a 39-word Statement and a two-sentence actor
Description; both requirements are otherwise clean.
"""

import json
import os

import pytest

from conftest import copy_fixture, run_check, run_fixture, run_json


def warnings_of(payload):
    return {w["rule"] for w in payload["warnings"]}


def test_collect_warnings_parses_the_spec_files_when_not_given_them(tmp_path):
    """`run()` passes the actors it already parsed; a standalone caller can
    still omit them and `collect_warnings` reads `spec/actors.md` itself."""
    from hamilton_core import check, model
    d = copy_fixture("warnings", tmp_path)
    reqs, _dups, _mal = model.extract(os.path.join(d, "spec", "requirements.md"))
    got = {w["rule"] for w in check.collect_warnings(d, reqs)}
    assert got == {"long-statement", "long-description"}


def test_warnings_fixture_is_green_but_warns(tmp_path):
    exit_code, payload = run_json("warnings", tmp_path)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["findings"] == []
    assert warnings_of(payload) == {"long-statement", "long-description"}


def test_long_statement_names_the_requirement_and_word_count(tmp_path):
    _, payload = run_json("warnings", tmp_path)
    w = next(w for w in payload["warnings"] if w["rule"] == "long-statement")
    assert w["file"] == "spec/requirements.md"
    assert "R-0001" in w["message"] and "39 words" in w["message"]
    assert "warning:" in w["message"]


def test_long_description_names_the_actor(tmp_path):
    _, payload = run_json("warnings", tmp_path)
    w = next(w for w in payload["warnings"] if w["rule"] == "long-description")
    assert w["file"] == "spec/actors.md"
    assert "A-0001" in w["message"]


def test_statement_word_limit_boundary(tmp_path):
    d = copy_fixture("warnings", tmp_path)
    twenty = "word " * 20
    twentyone = "word " * 21
    path = os.path.join(d, "spec", "requirements.md")
    open(path, "w").write(
        "# Requirements\n\n"
        f"## R-0001 A\nActor: A-0001\nStatement: {twenty.strip()}.\n"
        "Criteria:\n- AC1: x -> y\n\n"
        f"## R-0002 B\nActor: A-0001\nStatement: {twentyone.strip()}.\n"
        "Criteria:\n- AC1: x -> y\n")
    open(os.path.join(d, "tests", "covers.js"), "w").write(
        "// @covers R-0001/AC1\n// @covers R-0002/AC1\n")
    proc = run_check(d, "--json")
    payload = json.loads(proc.stdout)
    long_stmts = {w["message"].split("'s")[0].split()[-1]
                  for w in payload["warnings"] if w["rule"] == "long-statement"}
    # 20 words: ok (the "under 20"/">20" boundary); 21: warned
    assert long_stmts == {"R-0002"}


def test_single_sentence_description_with_and_does_not_warn(tmp_path):
    d = copy_fixture("warnings", tmp_path)
    open(os.path.join(d, "spec", "actors.md"), "w").write(
        "# Actors\n\n## A-0001\nName: Account Holder\n"
        "Description: Signs in and expects the session to persist and to end "
        "on logout (e.g. after seven days).\n")
    payload = json.loads(run_check(d, "--json").stdout)
    assert "long-description" not in {w["rule"] for w in payload["warnings"]}


def test_no_interface_fires_for_an_interior_node_without_one(tmp_path):
    """`model` R-0001 has children and an `Interface:`; drop it and the
    subsystem boundary is flagged -- as a warning, not a failure."""
    d = copy_fixture("model", tmp_path)
    path = os.path.join(d, "spec", "requirements.md")
    body = open(path).read().replace(
        "Interface: POST /customers taking a JSON payload, returning 201 and an id.\n",
        "")
    open(path, "w").write(body)
    proc = run_check(d, "--json")
    payload = json.loads(proc.stdout)
    assert proc.returncode in (0, 1)          # the drop itself adds no finding
    w = [w for w in payload["warnings"] if w["rule"] == "no-interface"]
    assert len(w) == 1 and "R-0001" in w[0]["message"]
    assert "no-interface" not in {f["rule"] for f in payload["findings"]}


def test_a_leaf_requirement_never_gets_no_interface(tmp_path):
    _, payload = run_json("model", tmp_path)
    # R-0004 and R-0007 are leaves; neither should be flagged
    flagged = {w["message"] for w in payload["warnings"]
               if w["rule"] == "no-interface"}
    assert not any("R-0004" in m or "R-0007" in m for m in flagged)


def test_warnings_do_not_change_a_failing_exit_code(tmp_path):
    d = copy_fixture("warnings", tmp_path)
    # break coverage: drop the tag file -> `uncovered` findings, exit 1
    os.remove(os.path.join(d, "tests", "covers.js"))
    proc = run_check(d, "--json")
    payload = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert "uncovered" in {f["rule"] for f in payload["findings"]}
    assert warnings_of(payload) == {"long-statement", "long-description"}


def test_warnings_are_not_findings(tmp_path):
    _, payload = run_json("warnings", tmp_path)
    for f in payload["findings"]:
        assert f["rule"] not in ("long-statement", "long-description",
                                 "no-interface")


@pytest.mark.parametrize("name", ["clean", "tree", "model", "stale", "uncovered"])
def test_conforming_fixtures_emit_no_warnings(name, tmp_path):
    _, payload = run_json(name, tmp_path)
    assert payload["warnings"] == [], name


def test_human_warnings_go_to_stderr_not_stdout(tmp_path):
    human = run_fixture("warnings", tmp_path)
    assert human.returncode == 0
    assert "warning: long-statement" in human.stderr
    assert "warning:" not in human.stdout
    assert "advisory, not failures" in human.stderr
