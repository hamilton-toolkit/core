"""`hamilton design` / `hamilton build` / `hamilton reverse` -- the session
driver. The rendering it drives lives in `console`.

Hamilton sets `.hamilton/phase`, prints a status banner, then drives the agent
session turn by turn, in process. It does not hand over the terminal, which is
what makes three things possible:

  * a wrongly-picked option can be taken back -> `Console.ask` renders the
    choices as a cursor list and sends nothing until Enter;
  * the session ends when the phase's workflow is done -> a `PhaseDone` event
    ends the loop and returns the engineer to their shell;
  * an interrupted session is not lost -> every turn boundary writes a
    `Checkpoint`, and the next launch offers to resume it.

`HAMILTON_SESSION` is exported before the agent starts, so a `design` / `build`
that an agent shells out to from inside a session is refused: the phase is
fixed for the session. As ever this stops drift, not a determined operator --
`hamilton check` in CI is the authoritative gate.

Written against `protocol` alone: the vendor SDK lives behind
`claude_sdk_adapter`, and nothing in this file knows which model is answering.

Exit: 0 on a normal session, 1 on a session error or a failed precondition,
2 when run outside a Hamilton project, 130 if interrupted.
"""

from __future__ import annotations

import asyncio
import os
import sys

from hamilton_core import guard as _guard
from hamilton_core import status as _status
from hamilton_core.session import protocol as P
from hamilton_core.session.claude_sdk_adapter import ClaudeSdkAdapter
from hamilton_core.session.console import Console

SESSION_ENV = "HAMILTON_SESSION"
PHASE_REL = ".hamilton/phase"

# The opening instruction each session starts on, so it begins working instead
# of waiting to be told "go". The real protocol lives in the `hamilton` skill;
# these only point at it.
KICKOFF = {
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

FOOTER = {
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

# What Hamilton offers once the agent says an iteration is complete. The
# summary it just gave is the record of that iteration; this is the same
# decision a fresh session would open with, minus losing the conversation --
# the agent keeps everything it has already read and ratified.
NEXT_STEPS = {
    "spec": (
        ("Specify another change",
         "The engineer has another change to the specification. Run the Specify "
         "review protocol from Phase 1: ask what the change is, plan it "
         "silently, state the size, then present it one item at a time and "
         "write each to `spec/` only on approval."),
        ("Decompose a requirement further",
         "The engineer wants to decompose an existing requirement into "
         "children. Render `hamilton tree`, ask which requirement to take, then "
         "run the review protocol for the new children and for the parent's "
         "`Interface:` line."),
        ("Check the tree adds up",
         "Render `hamilton tree` and read it upward: for each parent, ask "
         "whether its children add up to it. Report any gap you find, then run "
         "the review protocol for whatever the engineer decides to fix."),
    ),
    "build": (
        ("Take another build task",
         "The engineer has more for you to build. Ask what it is, then follow "
         "the Implement / Propagate a change workflow and get `hamilton check` "
         "green."),
        ("Re-run the gate",
         "Run `hamilton check` again and report what it says. If it is red, "
         "follow the Verify workflow until it is green."),
    ),
}
NEXT_STEPS["reverse"] = NEXT_STEPS["spec"]

NEXT_PROMPT = ("That iteration is done. What next? Pick a step, type your own, "
               "or finish the session.")

RESUME_KICKOFF = (
    "Resuming this Hamilton session after an interruption. Re-read the state "
    "you need (`hamilton status`, `hamilton tree`, `git diff spec/`), say in "
    "one or two lines where we had got to, and carry on from there -- do not "
    "restart the workflow from the top."
)


def _verb(phase: str) -> str:
    return "design" if phase == "spec" else "build"


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


def next_step(console: Console, kickoff_key: str) -> str | None:
    """Offer the step after a completed iteration. Returns the instruction to
    send the agent, or None to finish the session."""
    steps = NEXT_STEPS[kickoff_key]
    answer = console.choose(P.Question(
        NEXT_PROMPT,
        tuple(P.Choice(label, "") for label, _ in steps),
        "Iteration complete",
    ))
    if answer is None:
        return None
    # A label maps to its instruction; anything else is the engineer's own
    # next step, in their words, and is sent as-is.
    return dict(steps).get(answer, answer)


async def drive(root: str, phase: str, kickoff: str, adapter: P.AgentAdapter,
                console: Console, kickoff_key: str = "spec") -> int:
    """Run turns until the engineer finishes or ends the session.

    A `PhaseDone` is an *iteration* boundary, not the end: the agent has given
    its summary, and the engineer is offered the next step with the session --
    and everything the agent has already read and ratified -- still live.
    Checkpoints after every turn; that is the resume path.
    """
    cp = P.Checkpoint(phase=phase, session_ref=adapter.session_ref)
    text: str | None = kickoff
    rc = 0
    try:
        while text is not None:
            done = False
            # Runs until the turn yields; `Console` suspends it around anything
            # that reads or draws, including the questions the agent asks from
            # its own thread.
            console.start_working()
            async for ev in adapter.run_turn(text):
                if isinstance(ev, P.AgentText):
                    console.agent_text(ev.text)
                    body = ev.text.strip()
                    if body:
                        cp.last_summary = body.splitlines()[-1][:200]
                elif isinstance(ev, P.ToolDenied):
                    console.denial(ev.path, ev.reason)
                elif isinstance(ev, P.SessionError):
                    console.error(ev.message)
                    rc = 1
                elif isinstance(ev, P.PhaseDone):
                    done = True

            console.stop_working()
            cp.session_ref = adapter.session_ref
            cp.turns_completed += 1

            if console.aborted or rc:
                cp.save(root)           # interrupted: leave it resumable
                break

            if done:
                text = await asyncio.to_thread(next_step, console, kickoff_key)
                cp.done = text is None  # only a chosen finish completes it
                cp.save(root)
                continue

            cp.save(root)
            text = await asyncio.to_thread(console.prompt_turn)
    finally:
        console.stop_working()
        await adapter.close()
    return rc


def main(phase: str, *, verb: str | None = None,
         kickoff_key: str | None = None) -> int:
    verb = verb or _verb(phase)
    kickoff_key = kickoff_key or phase
    root = os.getcwd()
    console = Console()

    if not os.path.isdir(os.path.join(root, ".hamilton")):
        console.error(f"{verb}: no .hamilton/ here -- run from a Hamilton "
                      f"project root (`hamilton init` first).")
        return 2

    existing = os.environ.get(SESSION_ENV)
    if existing:
        console.error(f"{verb}: already inside a Hamilton session "
                      f"({SESSION_ENV}={existing!r}). A session's phase is "
                      f"fixed when it is launched; the agent cannot switch it. "
                      f"Exit this session and run from a plain shell.")
        return 1

    if kickoff_key == "reverse":
        n = _live_requirement_count(root)
        if n:
            console.error(f"{verb}: spec/requirements.md already has {n} "
                          f"requirement(s). `hamilton reverse` derives a first "
                          f"spec from an existing codebase; it will not run "
                          f"against a spec that already has content. Run "
                          f"`hamilton design` to extend the existing spec.")
            return 1

    with open(os.path.join(root, PHASE_REL), "w", encoding="utf-8") as fh:
        fh.write(phase)
    os.environ[SESSION_ENV] = phase

    console.banner(_status.render(root, phase))

    kickoff = KICKOFF[kickoff_key]
    resume_ref = None
    cp = P.Checkpoint.load(root)
    if cp and cp.resumable and cp.phase == phase and console.offer_resume(cp):
        resume_ref, kickoff = cp.session_ref, RESUME_KICKOFF

    console.note(f"hamilton {verb}: phase is '{phase}'; "
                 f"{'resumed' if resume_ref else 'new'} session.")

    adapter = ClaudeSdkAdapter(
        root=root,
        answerer=console.ask,
        write_policy=lambda target: _guard.decide(root, target),
        resume_ref=resume_ref,
    )
    try:
        rc = asyncio.run(drive(root, phase, kickoff, adapter, console,
                               kickoff_key))
    except KeyboardInterrupt:
        console.error("interrupted -- the checkpoint is kept, "
                      f"`hamilton {verb}` will offer to resume.")
        return 130

    console.say()
    console.note(FOOTER[kickoff_key])
    return rc
