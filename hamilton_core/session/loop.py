"""`hamilton design` / `hamilton reverse` / `hamilton validate` -- the session
driver. The rendering
it drives lives in `console`.

Hamilton sets `.hamilton/phase`, prints a status banner, then drives the agent
session turn by turn, in process. It does not hand over the terminal, which is
what makes three things possible:

  * a wrongly-picked option can be taken back -> `Console.ask` renders the
    choices as a cursor list and sends nothing until Enter;
  * the session ends when the phase's workflow is done -> a `PhaseDone` event
    ends the loop and returns the engineer to their shell;
  * an interrupted session is not lost -> every turn boundary writes a
    `Checkpoint`, and the next launch offers to resume it.

This drives the modes. A mode that needs more than the conversation --
`hamilton validate` runs the build between turns -- brings it as `Extras`.
Build is not a session: `hamilton build` is
a loop Hamilton runs itself (`hamilton_core.build`), because what comes next
there follows from `hamilton verify`, not from an agent's judgement.

`HAMILTON_SESSION` is exported before the agent starts, so a `design` /
`build` that an agent shells out to from inside a session is refused: the
phase is fixed for the session. As ever this stops drift, not a determined operator --
`hamilton verify` in CI is the authoritative gate.

What differs between the modes is defined once, in `modes`.

Written against `protocol` alone: the vendor SDK lives behind
`claude_sdk_adapter`, and nothing in this file knows which model is answering.

Exit: 0 on a normal session, 1 on a session error or a failed precondition,
2 when run outside a Hamilton project, 130 if interrupted.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Awaitable, Callable

from hamilton_core import guard as _guard
from hamilton_core import init as _init
from hamilton_core import phase as _phase
from hamilton_core import show as _show
from hamilton_core import status as _status
from hamilton_core.session import protocol as P
from hamilton_core.session.agent import Agent
from hamilton_core.session.claude_sdk_adapter import ClaudeSdkAdapter
from hamilton_core.session.console import Console, tokens
from hamilton_core.session.modes import Mode, Step

SESSION_ENV = "HAMILTON_SESSION"

NEXT_PROMPT = "That iteration is done. What next? Pick a step, or finish the session."

RESUME_KICKOFF = (
    "Resuming this Hamilton session after an interruption. Re-read the state "
    "you need (`hamilton status`, `hamilton show`, `git diff spec/`), say in "
    "one or two lines where we had got to, and carry on from there -- do not "
    "restart the workflow from the top."
)


@dataclass
class Extras:
    """What a mode adds to its session besides the conversation.

    `tools` are offered to the agent beside `ask_engineer`. `after_turn` is
    awaited after every turn, told whether the agent closed an iteration; the
    text it returns is sent to the agent next, instead of asking the engineer.
    `at_end` is awaited once the engineer has finished, and returns the exit
    code, or None to keep the session's own."""
    tools: tuple[P.Tool, ...] = ()
    after_turn: Callable[[bool], Awaitable[str | None]] | None = None
    at_end: Callable[[], Awaitable[int | None]] | None = None


def next_step(console: Console, mode: Mode, root: str) -> str | None:
    """Offer the step after a completed iteration. Returns the instruction to
    send the agent, or None to finish the session. A step the engineer backs
    out of shows the menu again."""
    steps = {s.label: s for s in mode.next_steps}
    while True:
        answer = console.choose(P.Question(
            NEXT_PROMPT,
            tuple(P.Choice(label) for label in steps),
            "Iteration complete",
        ))
        if answer is None:
            return None
        step = steps[answer]
        if step.template and _init.ensure(root, step.template):
            console.note(f"{step.template} scaffolded from the template.")
        if not step.picks_requirements:
            return step.instruction
        instruction = _change_picked_requirements(console, step, root)
        if instruction is not None:
            return instruction


def _change_picked_requirements(console: Console, step: Step, root: str) -> str | None:
    """Let the engineer pick requirements from the tree and say what should
    change. None if the tree is empty or they back out."""
    rows = _show.rows(root)
    if not rows:
        console.note("The spec has no requirements yet -- nothing to pick.")
        return None
    labels = {r["id"]: r["label"] for r in rows}
    options = [(r["id"], "  " * r["path"].count(".") + r["label"]) for r in rows]
    chosen = console.choose_many("Which requirements?", options)
    if not chosen:
        return None
    change = console.ask_text("What should change?")
    if change is None:
        return None
    return step.instruction.format(
        requirements=", ".join(labels[rid] for rid in chosen), change=change)


async def drive(root: str, mode: Mode, kickoff: str, adapter: P.AgentAdapter,
                console: Console, after_turn=None) -> int:
    """Run turns until the engineer finishes or ends the session.

    A `PhaseDone` is an *iteration* boundary, not the end: the agent has given
    its summary, and the engineer is offered the next step with the session --
    and everything the agent has already read and ratified -- still live.
    Checkpoints after every turn; that is the resume path. `after_turn` is
    `Extras.after_turn`.
    """
    agent = Agent(adapter)
    console.follow(agent.activity)
    cp = P.Checkpoint(phase=mode.phase, session_ref=agent.session_ref)
    text: str | None = kickoff
    rc = 0
    spent = 0                           # the session's tokens so far
    try:
        while text is not None:
            done = False
            # Runs until the turn yields; `Console` suspends it around anything
            # that reads or draws, including the questions the agent asks from
            # its own thread.
            console.start_working()
            async for ev in agent.run_turn(text):
                if isinstance(ev, P.AgentText):
                    console.agent_text(ev.text)
                    body = ev.text.strip()
                    if body:
                        cp.last_summary = body.splitlines()[-1][:200]
                elif isinstance(ev, P.SubagentDone):
                    console.subagent_done(ev.label, ev.ok, ev.elapsed)
                elif isinstance(ev, P.ToolDenied):
                    console.denial(ev.path, ev.reason)
                elif isinstance(ev, P.SessionError):
                    console.error(ev.message)
                    rc = 1
                elif isinstance(ev, P.PhaseDone):
                    done = True
                elif isinstance(ev, P.Spent):
                    spent += ev.tokens
                    console.say(console.paint.dim(
                        f"  {tokens(ev.tokens)} tokens · {tokens(spent)} this session"))

            console.stop_working()
            cp.session_ref = agent.session_ref
            cp.turns_completed += 1

            if console.aborted or rc:
                # resumable unless the engineer chose to finish
                cp.done = console.finished and not rc
                cp.save(root)
                break

            if after_turn is not None:
                cp.save(root)
                follow_up = await after_turn(done)
                console.follow(agent.activity)  # what it ran drew its own rows
                if follow_up is not None:
                    text = follow_up
                    continue

            if done:
                text = await asyncio.to_thread(next_step, console, mode, root)
                cp.done = text is None  # only a chosen finish completes it
                cp.save(root)
                continue

            cp.save(root)
            text = await asyncio.to_thread(console.next_message)
    finally:
        console.stop_working()
        await agent.close()
        if spent:
            console.say()
            console.say(console.paint.dim(f"Tokens {tokens(spent)} this session"))
    return rc


def main(mode: Mode) -> int:
    return session(os.getcwd(), mode, Console())


def session(root: str, mode: Mode, console: Console,
            extras: Callable[[], Extras] | None = None) -> int:
    """Launch `mode` in `root`: the checks, the phase, the banner, the
    resume offer, then the turns. `extras` is called once the checks have
    passed, for what the mode adds."""

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

    _phase.write(root, mode.phase)
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

    more = extras() if extras else Extras()
    adapter = ClaudeSdkAdapter(
        root=root,
        answerer=console.ask,
        write_policy=lambda target: _guard.decide(root, target),
        resume_ref=resume_ref,
        tools=more.tools,
    )

    async def run() -> int:
        rc = await drive(root, mode, kickoff, adapter, console, more.after_turn)
        if rc == 0 and more.at_end is not None:
            ended = await more.at_end()
            rc = rc if ended is None else ended
        return rc

    try:
        rc = asyncio.run(run())
    except KeyboardInterrupt:
        console.error("interrupted -- the checkpoint is kept, "
                      f"`hamilton {mode.name}` will offer to resume.")
        return 130

    console.say()
    console.note(mode.footer)
    return rc
