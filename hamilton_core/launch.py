"""`hamilton design` / `hamilton build` / `hamilton reverse` -- scope an agent
session to a phase.

Sets `.hamilton/phase`, prints a status banner, then runs the configured agent
(`agent_command` in `.hamilton/config`) **as a child process** and waits. When
the agent exits, Hamilton regains control, prints a one-line footer, and exits
with the agent's status.

`design` and `reverse` both set the `spec` phase; they differ only in the
kickoff. `reverse` (brownfield) points the agent at the "reverse-engineer the
spec from existing code" workflow and refuses to run when `spec/requirements.md`
already has real requirements -- it derives a *first* spec, it does not extend
one.

The phase is still fixed for the whole session. `HAMILTON_SESSION` is exported
before the agent starts, every child process inherits it, and `design` /
`build` refuse to run when it is already set -- so an agent that shells out to
`hamilton build` from inside a `design` session is blocked. This is the same
bar as the `PreToolUse` hook: it stops drift, not an operator who unsets the
variable or starts the agent directly. The authoritative gate is `hamilton
check` in CI. Note the reach differs: the launcher works for any
`agent_command`, but the `PreToolUse` hook that blocks
individual edits ships only for Claude Code.

`agent_command` is split on whitespace (`shlex`) and run directly -- no shell,
so flags work but env prefixes / pipes / `&&` do not. A one-line kickoff
instruction is appended as the final argument so the session starts working
immediately (`claude "<kickoff>"` opens already on that prompt); an agent that
does not take a positional prompt would need a wrapper script. The real
protocol lives in the `hamilton` skill; the kickoff only points at it.

Known edges, all benign:
  * A stale `HAMILTON_SESSION` left in the ambient environment (a shell rc, a
    parent process) makes `design` / `build` refuse to start with "already
    inside a session". Unset it.
  * `.hamilton/phase` is written before the agent starts, so a failed launch
    (bad `agent_command`) leaves the phase set. Harmless: the next `design` /
    `build` overwrites it and `hamilton check` ignores the phase.

Exit: the agent's status on a normal session; 130 if interrupted; 2 not a
Hamilton project; 1 already in a session, `agent_command` unset, or the agent
could not be launched.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys

from hamilton_core import status as _status

CONFIG_REL = ".hamilton/config"
PHASE_REL = ".hamilton/phase"
SESSION_ENV = "HAMILTON_SESSION"

_KICKOFF = {
    "spec": (
        "Start the Hamilton spec/design session now: follow the `hamilton` "
        "skill's Specify workflow from the top -- greet me, summarise the "
        "current spec state; if the spec is empty and `spec/vision.md` is still "
        "the scaffold, offer to help me draft the vision first, then move on to "
        "the root requirements; otherwise ask whether I want to draft the "
        "initial spec or modify/extend existing requirements. Then run the "
        "review protocol."
    ),
    "build": (
        "Start the Hamilton build session now: follow the `hamilton` skill's "
        "\"Propagate a change\" / \"Verify\" workflow immediately -- run "
        "`git diff spec/` and `hamilton check`, bring the code and tests to "
        "green, then give the closing summary. If this is the first build after "
        "`hamilton reverse` (no `.hamilton/verified`, most ACs uncovered, the "
        "spec only just landed in `git log -- spec`), follow \"Adopt an "
        "existing test suite\" instead. Do not wait for further instruction."
    ),
    "reverse": (
        "Start the Hamilton reverse (brownfield) session now: follow the "
        "`hamilton` skill's \"Reverse-engineer the spec from existing code\" "
        "workflow from the top -- survey the codebase and its git history, "
        "show me the frame you infer (what the system is for, its actors, the "
        "module map) and let me correct it, draft `spec/vision.md`, then derive "
        "the requirement tree module by module. Propose every piece and wait "
        "for my approval before writing it -- the spec captures intent and the "
        "load-bearing decisions, it does not restate the code."
    ),
}

_FOOTER = {
    "spec": ("hamilton design: session ended (phase 'spec'). Run `hamilton "
             "build` to implement the changes, or `hamilton design` again to "
             "keep specifying."),
    "build": ("hamilton build: session ended (phase 'build'). Run `hamilton "
              "check` to confirm the gate is green before opening a merge "
              "request."),
    "reverse": ("hamilton reverse: session ended (phase 'spec'). `hamilton "
                "check` will be red on `uncovered` until you run `hamilton "
                "build` -- that session binds the existing tests to the "
                "derived criteria. Run `hamilton design` to keep refining the "
                "spec."),
}


def _verb(phase: str) -> str:
    return "design" if phase == "spec" else "build"


def _agent_command(root: str) -> str | None:
    from hamilton_core.check import UsageError, read_config
    try:
        cfg = read_config(root)
    except UsageError:
        return None
    entry = cfg.get("agent_command")
    return entry[0].strip() if entry and entry[0].strip() else None


def _live_requirement_count(root: str) -> int:
    """Real requirements in spec/requirements.md -- 0 if the file is absent or
    holds only the fenced example. `hamilton reverse` refuses on a non-empty
    spec (it derives a *first* spec)."""
    from hamilton_core.check import REQ_REL, extract
    path = os.path.join(root, REQ_REL)
    if not os.path.isfile(path):
        return 0
    reqs, _dupes, _malformed = extract(path)
    return len(reqs)


def main(phase: str, *, verb: str | None = None,
         kickoff_key: str | None = None) -> int:
    verb = verb or _verb(phase)
    kickoff_key = kickoff_key or phase
    root = os.getcwd()
    if not os.path.isdir(os.path.join(root, ".hamilton")):
        print(f"hamilton {verb}: no .hamilton/ here -- run from a Hamilton "
              f"project root (`hamilton init` first).", file=sys.stderr)
        return 2

    existing = os.environ.get(SESSION_ENV)
    if existing:
        print(f"hamilton {verb}: already inside a Hamilton session "
              f"({SESSION_ENV}={existing!r}). A session's phase is fixed when "
              f"it is launched; the agent cannot switch it. Exit this session "
              f"and run `hamilton design` / `hamilton build` from a plain "
              f"shell.", file=sys.stderr)
        return 1

    if kickoff_key == "reverse":
        n = _live_requirement_count(root)
        if n:
            print(f"hamilton {verb}: spec/requirements.md already has {n} "
                  f"requirement(s). `hamilton reverse` derives a first spec "
                  f"from an existing codebase; it will not run against a spec "
                  f"that already has content. Run `hamilton design` to extend "
                  f"the existing spec.", file=sys.stderr)
            return 1

    agent = _agent_command(root)
    if not agent:
        print(f"hamilton {verb}: agent_command is not set in {CONFIG_REL}. Add "
              f"a line naming the command that starts your coding agent, e.g. "
              f"'agent_command=claude'.", file=sys.stderr)
        return 1
    try:
        argv = shlex.split(agent)
    except ValueError as exc:
        print(f"hamilton {verb}: agent_command in {CONFIG_REL} is not a valid "
              f"command line ({exc}).", file=sys.stderr)
        return 1
    if not argv:
        print(f"hamilton {verb}: agent_command in {CONFIG_REL} is empty.",
              file=sys.stderr)
        return 1
    argv.append(_KICKOFF[kickoff_key])

    with open(os.path.join(root, PHASE_REL), "w", encoding="utf-8") as fh:
        fh.write(phase)
    os.environ[SESSION_ENV] = phase

    print(_status.render(root, phase), file=sys.stderr)
    print(f"hamilton {verb}: phase is '{phase}'; launching {argv[0]}. Exit the "
          f"agent to end the session.", file=sys.stderr)

    # The agent owns the terminal, so it -- not this wrapper -- handles Ctrl-C.
    prev = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        proc = subprocess.run(argv, cwd=root)
    except FileNotFoundError as exc:
        print(f"hamilton {verb}: could not launch {argv[0]!r} ({exc}). Fix "
              f"agent_command in {CONFIG_REL}.", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGINT, prev)

    print(_FOOTER[kickoff_key], file=sys.stderr)
    return proc.returncode
