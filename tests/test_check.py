"""`hamilton check` against every fixture: the exact rule set, exit code and
counts. Assertions run against `--json`. Fixtures are copied to a temp dir
first (see conftest), so a test can edit them freely.

The model is one requirement tree (D-014): `spec/requirements.md` plus a flat
`spec/actors.md`. Every AC names its verification method (D-019).
"""

import re

import pytest

from conftest import copy_fixture, run_check, run_fixture, run_json, stamp

from hamilton_core import check as C

# fixture -> (exit code, {rule names}, finding count)
EXPECT = {
    "clean":              (0, set(),                       0),
    "tests-failed":       (1, {"tests-failed"},            1),
    "uncovered":          (1, {"uncovered"},               1),
    "orphan-tag":         (1, {"orphan-tag"},              1),
    "unreviewed":         (1, {"unreviewed"},              1),
    "malformed":          (1, {"malformed"},               4),
    "multi-violation":    (1, {"uncovered", "orphan-tag"}, 2),
    "orphan-requirement": (1, {"orphan-requirement"},      1),
    "dangling-ref":       (1, {"dangling-ref"},            1),
    "cyclic-parent":      (1, {"cyclic-parent"},           1),
    "no-method":          (1, {"no-method"},               1),
    "unknown-method":     (1, {"unknown-method"},          1),
    "no-method-paths":    (1, {"no-method-paths"},         1),
    "wrong-method":       (1, {"wrong-method"},            1),
    "retired-config":     (1, {"retired-config"},          1),
}
ONE_RULE = [n for n in EXPECT if n not in ("clean", "multi-violation")]
FAILING = [n for n in EXPECT if n != "clean"]

RULES = {"no-test-command", "tests-failed", "uncovered", "orphan-tag",
         "orphan-requirement", "dangling-ref", "cyclic-parent", "unreviewed",
         "malformed", "retired-config", "no-method", "unknown-method",
         "no-method-paths", "wrong-method"}


@pytest.mark.parametrize("name", list(EXPECT))
def test_fixture_reports_expected_rules(name, tmp_path):
    exit_code, payload = run_json(name, tmp_path)
    want_exit, want_rules, want_count = EXPECT[name]
    rules = {f["rule"] for f in payload["findings"]}
    assert exit_code == want_exit
    assert rules == want_rules
    assert len(payload["findings"]) == want_count
    assert payload["ok"] is (want_exit == 0)


def test_only_known_rules_are_ever_emitted(tmp_path):
    for name in EXPECT:
        _, payload = run_json(name, tmp_path)
        for f in payload["findings"]:
            assert f["rule"] in RULES, f


def test_clean_fixture_exits_zero_with_no_findings(tmp_path):
    exit_code, payload = run_json("clean", tmp_path)
    assert exit_code == 0
    assert payload["ok"] is True and payload["findings"] == []


@pytest.mark.parametrize("name", ONE_RULE)
def test_one_rule_fixtures_fire_exactly_one_rule(name, tmp_path):
    _, payload = run_json(name, tmp_path)
    rules = {f["rule"] for f in payload["findings"]}
    assert rules == EXPECT[name][1], f"{name}: {rules}"


def test_multi_violation_reports_both_problems_in_one_run(tmp_path):
    exit_code, payload = run_json("multi-violation", tmp_path)
    assert exit_code == 1
    assert sorted(f["rule"] for f in payload["findings"]) == ["orphan-tag", "uncovered"]


@pytest.mark.parametrize("name", FAILING)
def test_every_finding_carries_file_line_and_names_its_rule(name, tmp_path):
    _, payload = run_json(name, tmp_path)
    for f in payload["findings"]:
        assert isinstance(f["file"], str) and f["file"]
        assert isinstance(f["line"], int) and f["line"] > 0
        m = re.match(r"^(?P<loc>\S+:\d+): (?P<rule>[a-z-]+): ", f["message"])
        assert m, f["message"]
        assert m.group("rule") == f["rule"]
        assert m.group("loc") == f'{f["file"]}:{f["line"]}'


@pytest.mark.parametrize("name", FAILING)
def test_every_finding_message_states_expected_found_and_fix(name, tmp_path):
    _, payload = run_json(name, tmp_path)
    for f in payload["findings"]:
        low = f["message"].lower()
        assert "expected:" in low and "found:" in low and "fix:" in low, f["message"]


def test_counts_are_always_reported(tmp_path):
    for name, n_reqs, n_acs in [("clean", 1, 2), ("tests-failed", 1, 1),
                                ("multi-violation", 1, 2)]:
        _, payload = run_json(name, tmp_path)
        assert payload["requirements"] == n_reqs, name
        assert payload["acceptance_criteria"] == n_acs, name
    human = run_fixture("clean", tmp_path)
    assert "1 requirement(s), 2 acceptance criteria" in human.stderr


def test_tests_failed_points_at_the_config_and_reports_the_exit_status(tmp_path):
    _, payload = run_json("tests-failed", tmp_path)
    f = payload["findings"][0]
    assert f["rule"] == "tests-failed"
    assert f["file"] == ".hamilton/config"
    assert "exited 1" in f["message"]


def test_blank_test_command_is_its_own_rule_not_tests_failed(tmp_path):
    d = copy_fixture("clean", tmp_path)
    open(f"{d}/.hamilton/config", "w").write("test_command=\npaths.http=tests\npaths.unit=tests\n")
    proc = run_check(d, "--json")
    import json
    payload = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert {f["rule"] for f in payload["findings"]} == {"no-test-command"}
    assert "no suite to run" in payload["findings"][0]["message"]


def test_missing_test_command_key_is_no_test_command(tmp_path):
    d = copy_fixture("clean", tmp_path)
    open(f"{d}/.hamilton/config", "w").write("paths.http=tests\npaths.unit=tests\n")
    proc = run_check(d, "--json")
    import json
    payload = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert {f["rule"] for f in payload["findings"]} == {"no-test-command"}
    assert "key is absent" in payload["findings"][0]["message"]


def test_uncovered_names_the_uncovered_ac(tmp_path):
    _, payload = run_json("uncovered", tmp_path)
    f = payload["findings"][0]
    assert f["rule"] == "uncovered"
    assert f["file"] == "spec/requirements.md"
    assert f["req"] == "R-0001" and f["ac"] == "AC2"


def test_tag_outside_method_paths_does_not_count(tmp_path):
    # the uncovered fixture tags AC2 in notes/coverage.txt, which no method's paths hold
    _, payload = run_json("uncovered", tmp_path)
    assert {(f["req"], f["ac"]) for f in payload["findings"]} == {("R-0001", "AC2")}


def test_orphan_tag_points_at_the_source_tag_not_the_spec(tmp_path):
    _, payload = run_json("orphan-tag", tmp_path)
    f = payload["findings"][0]
    assert f["rule"] == "orphan-tag"
    assert f["file"].endswith("covers.rb")
    assert "R-0404" in f["message"]


def test_malformed_covers_all_four_manifestations(tmp_path):
    _, payload = run_json("malformed", tmp_path)
    joined = " ".join(f["message"] for f in payload["findings"])
    assert "unparseable line" in joined
    assert "no acceptance criteria" in joined
    assert "no Statement" in joined
    assert "twice" in joined                            # duplicate AC id


# -- the requirement tree (D-014) -------------------------------------- #

def test_orphan_requirement_wants_an_actor_on_a_root(tmp_path):
    _, payload = run_json("orphan-requirement", tmp_path)
    f = payload["findings"][0]
    assert f["rule"] == "orphan-requirement" and f["req"] == "R-0001"
    assert "Actor" in f["message"] and "Parent" in f["message"]


def test_dangling_ref_covers_parent_and_actor(tmp_path):
    _, payload = run_json("dangling-ref", tmp_path)
    assert payload["findings"][0]["rule"] == "dangling-ref"
    assert "R-0999" in payload["findings"][0]["message"]        # a dead Parent
    # a dead Actor goes through the same rule
    d = copy_fixture("dangling-ref", tmp_path)
    open(f"{d}/spec/requirements.md", "a").write(
        "\n## R-0003 Bad actor\nActor: A-0404\n"
        "Statement: names an actor that is not declared.\n- AC1: x -> y\n")
    open(f"{d}/tests/covers.js", "a").write("// @covers R-0003/AC1\n")
    stamp(d)
    import json
    payload = json.loads(run_check(d, "--json").stdout)
    msgs = " ".join(f["message"] for f in payload["findings"]
                    if f["rule"] == "dangling-ref")
    assert "A-0404" in msgs and "R-0999" in msgs


def test_cyclic_parent_shows_the_loop(tmp_path):
    _, payload = run_json("cyclic-parent", tmp_path)
    f = payload["findings"][0]
    assert f["rule"] == "cyclic-parent"
    assert "R-0001 -> R-0002 -> R-0001" in f["message"]


def test_retired_interface_field_is_recognised_and_ignored(tmp_path):
    """A pre-D-019 `Interface:` line must not make an otherwise clean spec
    fail, and raises no finding or warning."""
    d = copy_fixture("clean", tmp_path)
    body = open(f"{d}/spec/requirements.md").read().replace(
        "Actor: A-0001\n", "Actor: A-0001\nInterface: HTTP bearer token.\n")
    open(f"{d}/spec/requirements.md", "w").write(body)
    import json
    proc = run_check(d, "--json")
    payload = json.loads(proc.stdout)
    assert proc.returncode == 0
    assert payload["findings"] == [] and payload["warnings"] == []


def test_retired_component_field_is_recognised_and_ignored(tmp_path):
    """A pre-D-014 `Component:` line must not make an otherwise clean spec
    fail -- it is recognised and skipped (RETIRED_FIELDS)."""
    d = copy_fixture("clean", tmp_path)
    body = open(f"{d}/spec/requirements.md").read().replace(
        "Actor: A-0001\n", "Actor: A-0001\nComponent: C-0001\n")
    open(f"{d}/spec/requirements.md", "w").write(body)
    import json
    proc = run_check(d, "--json")
    payload = json.loads(proc.stdout)
    assert proc.returncode == 0
    assert payload["findings"] == []


def test_mutation_command_set_prints_a_notice_and_does_not_fail(tmp_path):
    d = copy_fixture("clean", tmp_path)
    with open(f"{d}/.hamilton/config", "a") as fh:
        fh.write("mutation_command=mutmut run\n")
    import json
    proc = run_check(d, "--json")
    payload = json.loads(proc.stdout)
    assert proc.returncode == 0                       # notice, not a failure
    assert payload["findings"] == []
    assert any("mutation_command" in n and "not implemented" in n
               for n in payload["notices"])
    human = run_check(d)
    assert "notice:" in human.stderr and "mutation_command" in human.stderr


def test_no_notice_when_mutation_command_is_absent_or_blank(tmp_path):
    _, payload = run_json("clean", tmp_path)
    assert payload["notices"] == []


def test_duplicate_requirement_id_is_malformed(tmp_path):
    """A repeated `## R-nnnn` is reported under `malformed` so id-uniqueness
    stays enforced."""
    d = copy_fixture("clean", tmp_path)
    open(f"{d}/spec/requirements.md", "a").write(
        "\n## R-0001\nStatement: a second block claiming R-0001.\n- AC1: x -> y\n")
    proc = run_check(d, "--json")
    import json
    payload = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert "malformed" in {f["rule"] for f in payload["findings"]}
    assert any("declared a second time" in f["message"] for f in payload["findings"])


def test_covers_tag_matched_regardless_of_comment_syntax(tmp_path):
    # clean uses // (js), orphan-tag # (rb), malformed # (sh) -- every AC these
    # fixtures declare is tagged, so none reports `uncovered`.
    for name in ("clean", "orphan-tag", "malformed"):
        _, payload = run_json(name, tmp_path)
        assert "uncovered" not in {f["rule"] for f in payload["findings"]}, name


def test_json_and_human_output_agree(tmp_path):
    human = run_fixture("multi-violation", tmp_path)
    _, payload = run_json("multi-violation", tmp_path)
    lines = [ln for ln in human.stdout.splitlines() if ln.strip()]
    assert len(lines) == len(payload["findings"])
    assert human.returncode == 1
    for f in payload["findings"]:
        assert f["message"] in human.stdout


def test_human_clean_run_is_silent_on_stdout_and_exits_zero(tmp_path):
    human = run_fixture("clean", tmp_path)
    assert human.returncode == 0
    assert human.stdout.strip() == ""
    assert "ok" in human.stderr


def test_missing_requirements_file_is_a_usage_error(tmp_path):
    proc = run_check(tmp_path, "--json")
    assert proc.returncode == 2
    import json
    assert "error" in json.loads(proc.stdout)


def test_missing_config_file_is_a_usage_error(tmp_path):
    d = copy_fixture("clean", tmp_path)
    import os
    os.remove(f"{d}/.hamilton/config")
    proc = run_check(d, "--json")
    assert proc.returncode == 2
    import json
    assert "error" in json.loads(proc.stdout)


def test_zero_requirements_fails(tmp_path):
    d = copy_fixture("clean", tmp_path)
    # keep only the fenced example, no live requirement
    open(f"{d}/spec/requirements.md", "w").write(
        "# Requirements\n\n```markdown\n## R-0001\nStatement: fenced.\n- AC1: a -> b\n```\n")
    proc = run_check(d, "--json")
    import json
    payload = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert payload["requirements"] == 0
    assert {f["rule"] for f in payload["findings"]} == {"malformed"}
    assert "no requirements" in payload["findings"][0]["message"].lower()


# -- verification methods (D-019) ------------------------------------- #

def _spec(d, criteria, methods="- **http** — requests to the running service.\n"
                                "- **unit** — one module in isolation.\n"):
    open(f"{d}/spec/requirements.md", "w").write(
        "# Requirements\n\n## Verification methods\n" + methods +
        "\n## R-0001\nActor: A-0001\nStatement: a rule.\nCriteria:\n" + criteria)


def _json(d):
    import json
    proc = run_check(d, "--json")
    return proc.returncode, json.loads(proc.stdout)


def _replace(path, old, new):
    body = open(path).read()
    assert old in body, old
    open(path, "w").write(body.replace(old, new))


def test_wrong_method_names_where_the_tag_is_and_where_it_must_be(tmp_path):
    _, payload = run_json("wrong-method", tmp_path)
    f = payload["findings"][0]
    assert (f["req"], f["ac"], f["methods"]) == ("R-0001", "AC2", ["browser"])
    assert "tests/covers.js:2" in f["message"]
    assert "paths.browser (tests/browser)" in f["message"]


def test_a_tag_under_the_method_paths_satisfies_it(tmp_path):
    d = copy_fixture("wrong-method", tmp_path)
    import os
    os.makedirs(f"{d}/tests/browser")
    open(f"{d}/tests/browser/wizard.js", "w").write("// @covers R-0001/AC2\n")
    stamp(d)
    code, payload = _json(d)
    assert code == 0 and payload["findings"] == []


def test_no_method_paths_is_reported_once_per_method(tmp_path):
    d = copy_fixture("no-method-paths", tmp_path)
    body = open(f"{d}/spec/requirements.md").read().replace(
        "-> 401 and no user data in the response body [http]",
        "-> 401 and no user data in the response body [browser]")
    open(f"{d}/spec/requirements.md", "w").write(body)
    _, payload = _json(d)
    assert [f["rule"] for f in payload["findings"]] == ["no-method-paths"]
    assert payload["findings"][0]["file"] == "spec/requirements.md"


def test_multi_method_ac_needs_a_tag_under_each_method(tmp_path):
    d = copy_fixture("clean", tmp_path)
    import os
    open(f"{d}/.hamilton/config", "w").write(
        "test_command=true\npaths.http=tests/http\npaths.unit=tests/unit\n")
    _spec(d, "- AC1: a -> b [unit, http]\n")
    os.remove(f"{d}/tests/covers.js")
    os.makedirs(f"{d}/tests/unit")
    open(f"{d}/tests/unit/a.js", "w").write("// @covers R-0001/AC1\n")
    stamp(d)
    _, payload = _json(d)
    f = payload["findings"]
    assert [x["rule"] for x in f] == ["uncovered"]
    assert f[0]["methods"] == ["unit", "http"] and "has no test for http" in f[0]["message"]
    os.makedirs(f"{d}/tests/http")
    open(f"{d}/tests/http/a.js", "w").write("// @covers R-0001/AC1\n")
    stamp(d)
    code, payload = _json(d)
    assert code == 0 and payload["findings"] == []


def test_manual_needs_no_tag_and_is_listed(tmp_path):
    d = copy_fixture("clean", tmp_path)
    _spec(d, "- AC1: a -> b [http]\n- AC2: looks calm -> approved [manual]\n")
    stamp(d)
    code, payload = _json(d)
    assert code == 0 and payload["findings"] == []
    assert payload["manual"] == ["R-0001/AC2"]
    human = run_check(d)
    assert "1 criterion verified manually, not by the gate" in human.stderr


def test_manual_is_reserved_and_needs_no_definition(tmp_path):
    d = copy_fixture("clean", tmp_path)
    _spec(d, "- AC1: a -> b [http]\n- AC2: c -> d [manual]\n",
          methods="- **http** — requests to the running service.\n")
    stamp(d)
    code, payload = _json(d)
    assert code == 0 and payload["findings"] == []


def test_no_manual_criteria_gives_an_empty_list(tmp_path):
    _, payload = run_json("clean", tmp_path)
    assert payload["manual"] == []


def test_changing_the_method_leaves_the_test_unreviewed(tmp_path):
    d = copy_fixture("clean", tmp_path)
    _replace(f"{d}/spec/requirements.md", "-> accepted [http]", "-> accepted [unit]")
    _, payload = _json(d)
    assert [(f["rule"], f["ac"]) for f in payload["findings"]] == [("unreviewed", "AC2")]


def test_methods_section_after_a_requirement_is_malformed(tmp_path):
    d = copy_fixture("clean", tmp_path)
    open(f"{d}/spec/requirements.md", "a").write(
        "\n## Verification methods\n- **unit** — one module.\n")
    _, payload = _json(d)
    assert any(f["rule"] == "malformed" and "Verification methods" in f["message"]
               for f in payload["findings"])


def test_findings_on_a_criterion_carry_its_methods(tmp_path):
    _, payload = run_json("uncovered", tmp_path)
    assert payload["findings"][0]["methods"] == ["http"]


# -- review suffixes (D-020) ------------------------------------------- #

WEB_TEST = """import { get } from './support.js';
function bearer(token) { return { Authorization: `Bearer ${token}` }; }

// @covers R-0001/AC1
it('rejects an expired token', async () => {
  const res = await get('/me', bearer(EXPIRED));
  expect(res.status).toBe(401);
  expect(res.body).toEqual({});
});
// @covers R-0001/AC2
it('accepts a token inside the skew window', async () => {
  expect((await get('/me', bearer(SKEWED))).status).toBe(200);
});
"""


def _web(tmp_path):
    """The clean fixture with a real test file: a preamble, then one section
    per AC -- every tag reviewed."""
    d = copy_fixture("clean", tmp_path)
    open(f"{d}/tests/covers.js", "w").write(WEB_TEST)
    stamp(d)
    return d


def test_unreviewed_points_at_the_tag_and_says_why(tmp_path):
    _, payload = run_json("unreviewed", tmp_path)
    f = payload["findings"][0]
    assert (f["rule"], f["file"], f["line"]) == ("unreviewed", "tests/covers.py", 1)
    assert (f["req"], f["ac"], f["methods"]) == ("R-0001", "AC1", ["http"])
    assert "(no review yet)" in f["message"] and "hamilton review R-0001/AC1" in f["message"]


def test_a_stamped_file_is_clean(tmp_path):
    code, payload = _json(_web(tmp_path))
    assert code == 0 and payload["findings"] == []


# a Statement or method change touches every AC; a test edit only its own
# section, unless it is in the preamble, which is part of every region
@pytest.mark.parametrize("rel, old, new, want", [
    ("spec/requirements.md", "-> accepted [http]", "-> accepted and logged [http]",
     {"AC2": "AC changed"}),
    ("spec/requirements.md", "Statement: The token validator rejects",
     "Statement: The validator rejects",
     {"AC1": "AC changed", "AC2": "AC changed"}),
    ("spec/requirements.md", "- **http** — requests to the running service",
     "- **http** — requests to a deployed service",
     {"AC1": "AC changed", "AC2": "AC changed"}),
    ("tests/covers.js", "toBe(200)", "toBeLessThan(500)",
     {"AC2": "test changed"}),
    ("tests/covers.js", "Bearer ${token}", "Token ${token}",
     {"AC1": "test changed", "AC2": "test changed"}),
])
def test_the_message_names_the_half_that_changed(tmp_path, rel, old, new, want):
    d = _web(tmp_path)
    _replace(f"{d}/{rel}", old, new)
    _, payload = _json(d)
    states = {f["ac"]: re.search(r"unreviewed \(([^)]+)\)", f["message"]).group(1)
              for f in payload["findings"]}
    assert states == want


def test_ac_and_test_both_changed(tmp_path):
    d = _web(tmp_path)
    _replace(f"{d}/spec/requirements.md", "-> accepted [http]", "-> accepted and logged [http]")
    _replace(f"{d}/tests/covers.js", "toBe(200)", "toBeLessThan(500)")
    _, payload = _json(d)
    [f] = payload["findings"]
    assert f["ac"] == "AC2" and "(AC and test changed)" in f["message"]


def test_whitespace_only_edits_keep_the_review(tmp_path):
    d = _web(tmp_path)
    _replace(f"{d}/tests/covers.js", "  expect(res.status).toBe(401);",
             "\n      expect(res.status).toBe(401);\n")
    _replace(f"{d}/spec/requirements.md", "-> accepted [http]", "->   accepted  [http]")
    code, payload = _json(d)
    assert code == 0 and payload["findings"] == []


def test_every_counting_tag_needs_its_own_review(tmp_path):
    d = _web(tmp_path)
    open(f"{d}/tests/more.js", "w").write("// @covers R-0001/AC1\nit('again', ...)\n")
    _, payload = _json(d)
    assert [(f["rule"], f["file"], f["ac"]) for f in payload["findings"]] == \
        [("unreviewed", "tests/more.js", "AC1")]


def test_check_never_writes(tmp_path):
    import os
    for name in ("clean", "unreviewed"):
        d = copy_fixture(name, tmp_path)

        def snapshot():
            return {os.path.join(p, f): open(os.path.join(p, f), "rb").read()
                    for p, _, fs in os.walk(d) for f in fs}
        before = snapshot()
        run_check(d)
        assert snapshot() == before, name


def test_a_leftover_verified_file_gets_a_notice(tmp_path):
    d = copy_fixture("clean", tmp_path)
    open(f"{d}/.hamilton/verified", "w").write("R-0001/AC1 sha256:00\n")
    code, payload = _json(d)
    assert code == 0 and payload["findings"] == []
    assert any(".hamilton/verified is no longer used" in n for n in payload["notices"])


def test_regions_are_preamble_plus_section():
    lines = ["import x", "", "// @covers R-0001/AC1", "// @covers R-0001/AC2",
             "test a", "// @covers R-0001/AC3 #abc.def", "test b"]
    r = C.regions(lines)
    assert set(r) == {3, 4, 6}
    assert r[3] == r[4]                                  # a stacked block shares one
    assert r[3] == "\n".join(lines[:5])                  # preamble + its section
    assert r[6] == "\n".join(lines[:2] + ["// @covers R-0001/AC3", "test b"])


def test_regions_without_preamble_or_tags():
    assert C.regions(["test only"]) == {}
    assert C.regions(["// @covers R-0001/AC1", "x"]) == {1: "// @covers R-0001/AC1\nx"}


def test_suffixes_are_stripped_before_hashing():
    bare = C.regions(["// @covers R-0001/AC1", "x"])
    suffixed = C.regions(["// @covers R-0001/AC1 #123456.abcdef", "x"])
    assert bare == suffixed


def test_renumbering_an_ac_changes_the_obligation():
    req = {"statement": "s.", "acs": {"AC2": {"text": "a -> b [unit]"},
                                      "AC3": {"text": "a -> b [unit]"}}}
    defined = {"unit": {"description": "one module."}}
    assert C.obligation("R-0001", "AC2", req, ["unit"], defined) != \
        C.obligation("R-0001", "AC3", req, ["unit"], defined)


def test_review_state_names_the_changed_half():
    assert C.review_state("aaaaaa.bbbbbb", "aaaaaa.bbbbbb") == C.REVIEWED
    assert C.review_state(None, "aaaaaa.bbbbbb") == C.NEVER_REVIEWED
    assert C.review_state("garbage", "aaaaaa.bbbbbb") == C.NEVER_REVIEWED
    assert C.review_state("000000.bbbbbb", "aaaaaa.bbbbbb") == C.AC_CHANGED
    assert C.review_state("aaaaaa.000000", "aaaaaa.bbbbbb") == C.TEST_CHANGED
    assert C.review_state("000000.000000", "aaaaaa.bbbbbb") == C.BOTH_CHANGED
