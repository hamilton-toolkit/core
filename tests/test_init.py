"""`hamilton init` -- scaffold generation and the post-init state.

After `init` a project has zero real requirements, so `hamilton check` there
now *fails* with a "no requirements" finding (it must not crash). A second
`init` refuses.
"""

import json
import os
import re
import subprocess
import sys

from conftest import REPO, run_check

COVERS_TAG_RE = re.compile(r"@covers\s+R-\d{4}/AC\d+")

SCAFFOLD = [
    "spec/vision.md",
    "spec/actors.md",
    "spec/requirements.md",
    ".hamilton/phase",
    ".hamilton/config",
    "AGENTS.md",
    "CLAUDE.md",
    ".claude/settings.json",
    ".claude/skills/hamilton/SKILL.md",
]


def run_init(cwd, *args):
    env = {**os.environ, "PYTHONPATH": REPO}
    return subprocess.run(
        [sys.executable, "-m", "hamilton_core", "init", *args],
        cwd=str(cwd), capture_output=True, text=True, env=env,
    )


def test_init_creates_every_scaffold_file(tmp_path):
    proc = run_init(tmp_path)
    assert proc.returncode == 0, proc.stderr
    for rel in SCAFFOLD:
        assert (tmp_path / rel).is_file(), f"missing {rel}"


def test_init_scaffolds_no_ci_pipeline_file(tmp_path):
    """A CI pipeline is project-specific and out of scope: `init` ships none."""
    assert run_init(tmp_path).returncode == 0
    for name in (".gitlab-ci.yml", ".github"):
        assert not (tmp_path / name).exists(), name


def test_claude_md_points_at_agents_md(tmp_path):
    """D-012: AGENTS.md holds the rules; CLAUDE.md is a one-line pointer."""
    run_init(tmp_path)
    claude = (tmp_path / "CLAUDE.md").read_text()
    assert "AGENTS.md" in claude and len(claude.splitlines()) <= 3
    agents = (tmp_path / "AGENTS.md").read_text()
    assert "Behaviour changes require a specification change first." in agents


def test_vision_md_is_scaffolded_and_not_an_entity_file(tmp_path):
    run_init(tmp_path)
    text = (tmp_path / "spec" / "vision.md").read_text()
    assert "Non-goals" in text
    assert not re.search(r"^##\s+[A-Z]-\d{4}\b", text, re.M)  # no entities


def test_config_has_a_commented_mutation_command(tmp_path):
    run_init(tmp_path)
    cfg = (tmp_path / ".hamilton" / "config").read_text()
    assert re.search(r"^#\s*mutation_command=", cfg, re.M)
    assert not re.search(r"^mutation_command=", cfg, re.M)   # commented, not active


def test_init_does_not_create_a_ledger_or_verified_file(tmp_path):
    run_init(tmp_path)
    assert not (tmp_path / ".hamilton" / "falsification.jsonl").exists()
    assert not (tmp_path / ".hamilton" / "verified").exists()


def test_init_then_check_reports_no_requirements_without_crashing(tmp_path):
    assert run_init(tmp_path).returncode == 0
    proc = run_check(tmp_path, "--json")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["requirements"] == 0
    assert {f["rule"] for f in payload["findings"]} == {"malformed"}
    assert "no requirements" in payload["findings"][0]["message"].lower()


def test_second_init_refuses_and_leaves_files_untouched(tmp_path):
    assert run_init(tmp_path).returncode == 0
    (tmp_path / ".hamilton" / "phase").write_text("SENTINEL-DO-NOT-TOUCH")
    before = {rel: (tmp_path / rel).read_bytes() for rel in SCAFFOLD}

    proc = run_init(tmp_path)
    assert proc.returncode != 0
    assert ".hamilton" in proc.stderr

    after = {rel: (tmp_path / rel).read_bytes() for rel in SCAFFOLD}
    assert after == before
    assert (tmp_path / ".hamilton" / "phase").read_text() == "SENTINEL-DO-NOT-TOUCH"


def test_init_accepts_a_path_argument_and_creates_missing_dirs(tmp_path):
    target = tmp_path / "does" / "not" / "exist"
    proc = run_init(tmp_path, str(target))
    assert proc.returncode == 0, proc.stderr
    for rel in SCAFFOLD:
        assert (target / rel).is_file(), f"missing {rel}"


def test_phase_file_contains_spec(tmp_path):
    run_init(tmp_path)
    assert (tmp_path / ".hamilton" / "phase").read_text().strip() == "spec"


def test_config_has_test_command_and_method_paths(tmp_path):
    run_init(tmp_path)
    cfg = (tmp_path / ".hamilton" / "config").read_text()
    assert re.search(r"^test_command=", cfg, re.M)
    assert re.search(r"^# paths\.\w+=", cfg, re.M)       # an example, not a guess
    assert not re.search(r"^test_paths=", cfg, re.M)


def test_settings_json_wires_pretooluse_guard_hook(tmp_path):
    run_init(tmp_path)
    data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    groups = data["hooks"]["PreToolUse"]
    assert any(
        "Write" in g["matcher"] and "Edit" in g["matcher"]
        and any(h["type"] == "command" and h["command"] == "hamilton guard"
                for h in g["hooks"])
        for g in groups
    ), data


def test_requirements_template_has_no_live_requirement_block(tmp_path):
    """Every `## R-nnnn` in the shipped template is inside a fenced code block,
    so `hamilton check` extracts zero requirements from it."""
    run_init(tmp_path)
    text = (tmp_path / "spec" / "requirements.md").read_text()
    live, in_fence = [], False
    for line in text.splitlines():
        if line.strip().startswith("```") or line.strip().startswith("~~~"):
            in_fence = not in_fence
            continue
        if not in_fence:
            live.append(line)
    assert not re.search(r"^##\s+R-\d{4}\b", "\n".join(live), re.M)


def test_no_scaffold_file_matches_the_covers_tag_regex(tmp_path):
    run_init(tmp_path)
    for dirpath, _, names in os.walk(tmp_path):
        for name in names:
            full = os.path.join(dirpath, name)
            text = open(full, encoding="utf-8").read()
            assert not COVERS_TAG_RE.search(text), f"live @covers tag in {full}"
