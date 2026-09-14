"""`hamilton init [path]` -- scaffold a project.

Copies the tree under ``templates/`` into the target directory (dot-prefixing
the names that cannot be package directory entries). Refuses if ``.hamilton/``
already exists -- that directory is the initialised-project sentinel. Exit: 0
scaffolded, 1 refused.
"""

from __future__ import annotations

import os
import sys

TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

# template-relative path -> scaffold-relative path
LAYOUT = {
    "spec/vision.md": "spec/vision.md",
    "spec/actors.md": "spec/actors.md",
    "spec/requirements.md": "spec/requirements.md",
    "hamilton/phase": ".hamilton/phase",
    "hamilton/config": ".hamilton/config",
    # Agent instructions: AGENTS.md holds the rules, CLAUDE.md points at it.
    "AGENTS.md": "AGENTS.md",
    "CLAUDE.md": "CLAUDE.md",
    "claude/settings.json": ".claude/settings.json",
    # Workflow prompt: neutral Markdown in prompts/, copied into the skill dir.
    "prompts/hamilton.md": ".claude/skills/hamilton/SKILL.md",
}

# Framework-owned files `hamilton upgrade` brings up to date: everything init
# writes except the project's own files (spec/, the phase file, the config) and
# the verified ledger, which the project owns outright.
MANAGED = tuple(
    dst for dst in LAYOUT.values()
    if not dst.startswith("spec/")
    and dst not in (".hamilton/phase", ".hamilton/config", ".hamilton/verified")
)
SRC_FOR = {dst: src for src, dst in LAYOUT.items()}

# Scaffold paths a past `init` wrote and this one no longer does; `hamilton
# upgrade` deletes them from an older project. Only framework-authored files
# with no project value once retired belong here -- the `/spec` and `/build`
# slash commands, replaced by `hamilton design` / `hamilton build`.
#
# NOT here: `.gitlab-ci.yml` (an older `init` wrote a placeholder pipeline).
# A pipeline is the project's own (D-005); by now it may be a real one, so
# `upgrade` leaves it -- it is simply no longer a managed file. A pre-D-005
# project should delete the placeholder by hand.
#
# NOT here either: `spec/components.md` and `spec/modules.md`, which a pre-D-014
# `init` wrote. Those live under spec/, which the project owns and `upgrade`
# never touches; `hamilton check` ignores them. Delete them by hand once their
# content has moved into the requirement tree.
RETIRED = (
    ".claude/commands/spec.md",
    ".claude/commands/build.md",
)


def main(path: str | None = None) -> int:
    root = os.path.abspath(path or ".")
    if os.path.exists(os.path.join(root, ".hamilton")):
        print(f"hamilton init: {os.path.join(root, '.hamilton')} already exists; "
              f"this project is already initialised. Nothing written. Remove "
              f".hamilton/ by hand to re-scaffold.", file=sys.stderr)
        return 1

    for src, dst in LAYOUT.items():
        with open(os.path.join(TEMPLATES, src), "r", encoding="utf-8") as fh:
            body = fh.read()
        target = os.path.join(root, dst)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(body)

    print(f"hamilton init: scaffolded {root}\n\n"
          f"Run `hamilton design` to write the specification with an AI agent's "
          f"help.\nPrefer to write it by hand? Start from the files in spec/ -- "
          f"each explains what it should contain.\nAdopting an existing "
          f"codebase? Run `hamilton reverse` to derive the first spec from the "
          f"code.")
    return 0
