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

What differs between the modes is defined once, in `modes`.

Written against `protocol` alone: the vendor SDK lives behind
`claude_sdk_adapter`, and nothing in this file knows which model is answering.

Exit: 0 on a normal session, 1 on a session error or a failed precondition,
2 when run outside a Hamilton project, 130 if interrupted.
"""

from __future__ import annotations

import asyncio
import os

from hamilton_core import guard as _guard
from hamilton_core import status as _status
from hamilton_core.session import protocol as P
from hamilton_core.session.claude_sdk_adapter import ClaudeSdkAdapter
from hamilton_core.session.console import Console
from hamilton_core.session.modes import Mode

SESSION_ENV = "HAMILTON_SESSION"
PHASE_REL = ".hamilton/phase"

NEXT_PROMPT = "That iteration is done. What next? Pick a step, or finish the session."

RESUME_KICKOFF = (
    "Resuming this Hamilton session after an interruption. Re-read the state "
    "you need (`hamilton status`, `hamilton tree`, `git diff spec/`), say in "
    "one or two lines where we had got to, and carry on from there -- do not "
    "restart the workflow from the top."
)


def next_step(console: Console, mode: Mode) -> str | None:
    """Offer the step after a completed iteration. Returns the instruction to
    send the agent, or None to finish the session."""
    steps = mode.next_steps
    answer = console.choose(P.Question(
        NEXT_PROMPT,
        tuple(P.Choice(label, "") for label, _ in steps),
        "Iteration complete",
    ))
    if answer is None:
        return None
    return dict(steps)[answer]


async def drive(root: str, mode: Mode, kickoff: str, adapter: P.AgentAdapter,
                console: Console) -> int:
    """Run turns until the engineer finishes or ends the session.

    A `PhaseDone` is an *iteration* boundary, not the end: the agent has given
    its summary, and the engineer is offered the next step with the session --
    and everything the agent has already read and ratified -- still live.
    Checkpoints after every turn; that is the resume path.
    """
    cp = P.Checkpoint(phase=mode.phase, session_ref=adapter.session_ref)
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
                text = await asyncio.to_thread(next_step, console, mode)
                cp.done = text is None  # only a chosen finish completes it
                cp.save(root)
                continue

            cp.save(root)
            text = await asyncio.to_thread(console.prompt_turn)
    finally:
        console.stop_working()
        await adapter.close()
    return rc


def main(mode: Mode) -> int:
    root = os.getcwd()
    console = Console()

    def refuse(message: str, rc: int = 1) -> int:
        console.error(f"{mode.name}: {message}")
        return rc

    if not os.path.isdir(os.path.join(root, ".hamilton")):
        return refuse("no .hamilton/ here -- run from a Hamilton project root "
                      "(`hamilton init` first).", 2)

    existing = os.environ.get(SESSION_ENV)
    if existing:
        return refuse(f"already inside a Hamilton session ({SESSION_ENV}="
                      f"{existing!r}). A session's phase is fixed when it is "
                      f"launched; the agent cannot switch it. Exit this "
                      f"session and run from a plain shell.")

    problem = mode.precheck(root) if mode.precheck else None
    if problem:
        return refuse(problem)

    with open(os.path.join(root, PHASE_REL), "w", encoding="utf-8") as fh:
        fh.write(mode.phase)
    os.environ[SESSION_ENV] = mode.phase

    console.banner(_status.render(root, mode.phase))

    kickoff = mode.kickoff
    resume_ref = None
    cp = P.Checkpoint.load(root)
    if (cp and cp.resumable and cp.phase == mode.phase
            and console.offer_resume(cp)):
        resume_ref, kickoff = cp.session_ref, RESUME_KICKOFF

    console.note(f"hamilton {mode.name}: phase is '{mode.phase}'; "
                 f"{'resumed' if resume_ref else 'new'} session.")

    adapter = ClaudeSdkAdapter(
        root=root,
        answerer=console.ask,
        write_policy=lambda target: _guard.decide(root, target),
        resume_ref=resume_ref,
    )
    try:
        rc = asyncio.run(drive(root, mode, kickoff, adapter, console))
    except KeyboardInterrupt:
        console.error("interrupted -- the checkpoint is kept, "
                      f"`hamilton {mode.name}` will offer to resume.")
        return 130

    console.say()
    console.note(mode.footer)
    return rc
