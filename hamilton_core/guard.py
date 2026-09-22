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
  `hamilton verify` in CI, reviewed against the config diff, is the backstop.

No phase file -> not a Hamilton project -> allow. Any other phase value ->
fail closed.

`suffix_denial(tool_name, tool_input, root)` is the second rule, in either
phase: only the reviewer in `hamilton build` writes a review suffix (D-020). A
Write / Edit / MultiEdit that introduces or changes one is refused. An
unchanged suffix passes, so rewriting a file whole still works, and removing
one is allowed -- that only makes the test unreviewed. Hamilton writes the
reviewer's suffix itself, not through an agent's tool call, so the rule never
sees it. A shell write gets around it, as it gets around every guard rule; CI
and the PR diff are the backstop.
"""
import os
import sys
import json
from collections import Counter

from hamilton_core import phase as _phase
from hamilton_core.verify import TAG_RE

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


def _suffixed(text: str) -> Counter:
    """(R-id, AC-id, suffix) for each tag in ``text`` that carries a suffix.
    Line by line, as `check.scan` reads them: a suffix never spans a line
    break, so a tag followed by a line starting `#include` has none."""
    return Counter(m.groups() for line in (text or "").splitlines()
                   for m in TAG_RE.finditer(line) if m.group(3))


def _on_disk(root: str, target: str) -> str:
    try:
        with open(os.path.join(root, target), encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return ""


def _edited(text: str, edits) -> str | None:
    """``text`` after the Edit / MultiEdit ``edits``, or None when one of
    them does not apply (the tool would then fail anyway)."""
    for e in edits:
        old, new = e.get("old_string") or "", e.get("new_string") or ""
        if not old or old not in text:
            return None
        text = text.replace(old, new, -1 if e.get("replace_all") else 1)
    return text


def suffix_denial(tool_name: str, tool_input: dict, root: str) -> str | None:
    """None, or the denial for a write that introduces or changes a review
    suffix: the file as the write would leave it holds a suffixed tag that the
    file on disk does not."""
    if _phase.read(root) is None:
        return None  # not a Hamilton project
    before = _on_disk(root, target_of(tool_input) or "")
    if tool_name == "Write":
        after = tool_input.get("content")
    elif tool_name in ("Edit", "MultiEdit"):
        edits = tool_input.get("edits") if tool_name == "MultiEdit" else [tool_input]
        after = _edited(before, edits or ())
        if after is None:
            # judge the edit on its own strings: a suffix in a new_string
            # that is not in its old_string
            before = "".join(e.get("old_string") or "" for e in edits or ())
            after = "".join(e.get("new_string") or "" for e in edits or ())
    else:
        return None
    forged = _suffixed(after) - _suffixed(before)
    if not forged:
        return None
    tags = ", ".join(f"'@covers {r}/{a} #{s}'" for r, a, s in sorted(forged))
    return (f"this write adds or changes a review suffix ({tags}). Only "
            f"the reviewer in `hamilton build` writes a suffix, when it passes "
            f"the test. Leave the suffix as it was, or drop it: the build has "
            f"the test reviewed.")


def main(argv=None) -> int:
    try:
        payload = json.load(sys.stdin)
        tool_input = payload.get("tool_input") or {}
        target = target_of(tool_input)
    except (ValueError, AttributeError):
        return 0
    if not target:
        return 0  # a non-path tool slipped the matcher -- allow
    root = os.getcwd()
    msg = (decide(root, target)
           or suffix_denial(payload.get("tool_name") or "", tool_input, root))
    if msg is None:
        return 0
    print(msg, file=sys.stderr)
    return 2
