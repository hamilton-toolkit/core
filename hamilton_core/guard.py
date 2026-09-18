"""`hamilton guard` -- phase-gate policy, and its PreToolUse hook backend.

`decide(root, target)` is the policy itself: it returns `None` to allow a write
to `target`, or the denial message to refuse it. Two call sites share it -- this
module's `main()`, the subprocess hook backend Claude Code invokes with a JSON
payload on stdin, and the in-process permission callback the session driver
installs (`hamilton_core.session`). One implementation, so the two cannot drift;
in a Hamilton session both run, and agree.

`main()` blocks (reason on stderr, exit 2 -- the hook contract's block-and-tell
path) a Write / Edit / MultiEdit / NotebookEdit whose target the current
`.hamilton/phase` forbids:

  spec phase   -> only `spec/` is writable
  build phase  -> everything is writable except `spec/`, `.hamilton/`,
                  `.claude/` and any file named `AGENTS.md` / `CLAUDE.md`
                  (root or nested -- both are loaded as agent instructions)
                  -- the spec, the tool's state, the hook/skill config, and
                  the rules the agent is meant to follow. An agent must not
                  edit what constrains it.

  build phase exception: `.hamilton/config` IS writable. Which test framework
  runs and where the tests for each verification method live are build-time
  decisions, and those are its keys (`test_command`, `paths.<method>`). A
  path hook cannot lock individual lines, so the whole file is writable in
  `build`. The trade is deliberate;
  `hamilton check` in CI, reviewed against the config diff, is the backstop.

No phase file -> not a Hamilton project -> allow. Any other phase value ->
fail closed.
"""
import os
import sys
import json

from hamilton_core import phase as _phase

LOCKED_IN_BUILD_DIRS = ("spec", ".hamilton", ".claude")
LOCKED_IN_BUILD_FILES = ("AGENTS.md", "CLAUDE.md")
BUILD_WRITABLE = (os.path.join(".hamilton", "config"),)


def _inside(root: str, target: str) -> bool:
    root, target = os.path.realpath(root), os.path.realpath(target)
    return target == root or target.startswith(root + os.sep)


def decide(root: str, target: str) -> str | None:
    """The phase-gate policy. `None` allows the write; a string is the denial
    message. `target` may be relative (resolved against `root`) or absolute.

    No `.hamilton/phase` means this is not a Hamilton project -- allow."""
    target = os.path.normpath(os.path.join(root, target))  # abs wins in join
    phase = _phase.read(root)
    if phase is None:
        return None  # no .hamilton/phase -- not a Hamilton project

    if phase not in ("spec", "build"):
        return (f".hamilton/phase holds {phase!r}, not 'spec' or 'build'; the phase "
                f"gate cannot function. Fix .hamilton/phase.")
    if phase == "spec":
        if _inside(os.path.join(root, "spec"), target):
            return None
        return (f"phase is 'spec': only spec/ is writable, so {target} cannot be "
                f"written. Run `hamilton build` (from a plain shell) to switch phase.")
    # build
    if any(os.path.realpath(target) == os.path.realpath(os.path.join(root, w))
           for w in BUILD_WRITABLE):
        return None  # test_command / paths.<method> are build-time decisions
    locked = next((d for d in LOCKED_IN_BUILD_DIRS
                   if _inside(os.path.join(root, d), target)), None)
    if locked is None and os.path.basename(target) in LOCKED_IN_BUILD_FILES:
        locked = os.path.basename(target)  # AGENTS.md / CLAUDE.md, root or nested
    if locked is None:
        return None
    return (f"phase is 'build': {locked} is read-only, so {target} cannot be "
            f"written. Run `hamilton design` (from a plain shell) to switch phase.")


def target_of(tool_input: dict) -> str | None:
    """The path a write tool is about to write, from its input."""
    return tool_input.get("file_path") or tool_input.get("notebook_path")


def main(argv=None) -> int:
    try:
        target = target_of(json.load(sys.stdin).get("tool_input") or {})
    except (ValueError, AttributeError):
        return 0
    if not target:
        return 0  # a non-path tool slipped the matcher -- allow
    msg = decide(os.getcwd(), target)
    if msg is None:
        return 0
    print(msg, file=sys.stderr)
    return 2
