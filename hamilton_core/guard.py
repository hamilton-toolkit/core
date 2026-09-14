"""`hamilton guard` -- phase-gate PreToolUse hook backend.

Blocks (reason on stderr, exit 2 -- the hook contract's block-and-tell path) a
Write / Edit / MultiEdit / NotebookEdit whose target the current
`.hamilton/phase` forbids:

  spec phase   -> only `spec/` is writable
  build phase  -> everything is writable except `spec/`, `.hamilton/`,
                  `.claude/` and any file named `AGENTS.md` / `CLAUDE.md`
                  (root or nested -- both are loaded as agent instructions)
                  -- the spec, the tool's state, the hook/skill config, and
                  the rules the agent is meant to follow. An agent must not
                  edit what constrains it.

  build phase exception: `.hamilton/config` IS writable. Which test framework
  runs and where the tests live are build-time decisions, and those are its
  two keys (`test_command`, `test_paths`). A path hook cannot lock individual
  lines, so the whole file is writable in `build` -- an agent could also
  rewrite `agent_command` (read only at the next launch, and visible in
  `git diff`). The trade is deliberate; `hamilton check` in CI, reviewed
  against the config diff, is the backstop.

No phase file -> not a Hamilton project -> allow. Any other phase value ->
fail closed.
"""
import os
import sys
import json

LOCKED_IN_BUILD_DIRS = ("spec", ".hamilton", ".claude")
LOCKED_IN_BUILD_FILES = ("AGENTS.md", "CLAUDE.md")
BUILD_WRITABLE = (os.path.join(".hamilton", "config"),)


def _inside(root: str, target: str) -> bool:
    root, target = os.path.realpath(root), os.path.realpath(target)
    return target == root or target.startswith(root + os.sep)


def main(argv=None) -> int:
    try:
        ti = json.load(sys.stdin).get("tool_input") or {}
        target = ti.get("file_path") or ti.get("notebook_path")
    except (ValueError, AttributeError):
        return 0
    if not target:
        return 0  # a non-path tool slipped the matcher -- allow
    root = os.getcwd()
    target = os.path.normpath(os.path.join(root, target))  # abs wins in join
    try:
        phase = open(os.path.join(root, ".hamilton", "phase"), encoding="utf-8").read().strip()
    except OSError:
        return 0  # no .hamilton/phase -- not a Hamilton project

    if phase not in ("spec", "build"):
        msg = (f".hamilton/phase holds {phase!r}, not 'spec' or 'build'; the phase "
               f"gate cannot function. Fix .hamilton/phase.")
    elif phase == "spec":
        if _inside(os.path.join(root, "spec"), target):
            return 0
        msg = (f"phase is 'spec': only spec/ is writable, so {target} cannot be "
               f"written. Run `hamilton build` (from a plain shell) to switch phase.")
    else:  # build
        if any(os.path.realpath(target) == os.path.realpath(os.path.join(root, w))
               for w in BUILD_WRITABLE):
            return 0  # test_command / test_paths are build-time decisions
        locked = next((d for d in LOCKED_IN_BUILD_DIRS
                       if _inside(os.path.join(root, d), target)), None)
        if locked is None and os.path.basename(target) in LOCKED_IN_BUILD_FILES:
            locked = os.path.basename(target)  # AGENTS.md / CLAUDE.md, root or nested
        if locked is None:
            return 0
        msg = (f"phase is 'build': {locked} is read-only, so {target} cannot be "
               f"written. Run `hamilton design` (from a plain shell) to switch phase.")
    print(msg, file=sys.stderr)
    return 2
