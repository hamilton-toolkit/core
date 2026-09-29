"""`hamilton validate` -- the second gate: the engineer tries the software.

`hamilton verify` proves the software does what the spec says. Whether the
spec says the right thing, and whether the software looks the way it was
meant to, only a person trying it can tell. The engineer keeps `hamilton run`
going in another terminal and reports what they find; the session sorts each
finding the way the `hamilton` skill's Validate workflow says:

  presentation   fixed in the session -- it is validated, never verified;
  bug            the spec is right, the code is not: a test tagged to the
                 criterion it breaks, failing first, then the fix;
  spec           wrong or incomplete: the agent asks for a spec change
                 (`change_spec`). On the engineer's word Hamilton switches the
                 phase to spec, the same conversation runs the Specify review
                 protocol, and when it is written Hamilton switches back.

What comes after a turn follows from `hamilton verify`, not from the agent:
when the gate without the suite has findings -- a spec change, a new test --
Hamilton runs the build loop in process before the engineer goes on, and
tells the agent how it went. A presentation fix leaves the gate as it was
and costs no build. When the engineer finishes, a session that changed the
project since the last build ends with one more, so the suite has checked
what it changed.

The session is `loop.session` with the `Extras` this module brings.
"""

from __future__ import annotations

import asyncio
import os

from hamilton_core import build as _build
from hamilton_core import phase as _phase
from hamilton_core import tree as _tree
from hamilton_core import verify as _verify
from hamilton_core.session import loop as _loop
from hamilton_core.session import protocol as P
from hamilton_core.session.console import Console
from hamilton_core.session.modes import VALIDATE

CHANGE_SPEC = "change_spec"
SPECIFY_NOW = "Specify it now"
NOT_NOW = "Not now"

_CHANGE_SPEC_DESCRIPTION = (
    "Ask to change the specification, when a finding shows the spec is wrong "
    "or incomplete -- the behaviour the engineer expects is not what a "
    "criterion says, or no requirement asks for it. `finding` is what the "
    "engineer found; `change` is the requirement or criterion you would add "
    "or change, in a sentence. Hamilton asks the engineer; if they agree, the "
    "phase switches to spec and you run the Specify review protocol for it."
)


class Validation:
    """What `hamilton validate` does between and after the turns."""

    def __init__(self, root: str, console: Console, worker: P.Worker,
                 judge: P.Judge) -> None:
        self.root = root
        self.console = console
        self.worker = worker
        self.judge = judge
        self.checked = _tree.snapshot(root)     # the tree the last build saw

    def extras(self) -> _loop.Extras:
        return _loop.Extras(
            tools=(P.Tool(CHANGE_SPEC, _CHANGE_SPEC_DESCRIPTION,
                          {"finding": str, "change": str}, self.change_spec),),
            after_turn=self.after_turn,
            at_end=self.at_end)

    async def change_spec(self, args: dict) -> str:
        """The agent's request to change the spec, put to the engineer."""
        if _phase.read(self.root) == "spec":
            return "The phase is spec already: carry on with the review protocol."
        change = str(args.get("change") or "").strip()
        finding = str(args.get("finding") or "").strip()
        paint = self.console.paint
        self.console.say()
        self.console.say(paint.heading("── The spec has to change ──"))
        if finding:
            self.console.say(f"  {paint.bold('Finding')}  {finding}")
        self.console.say(f"  {paint.bold('Change')}   {change}")
        answer = await asyncio.to_thread(self.console.choose, P.Question(
            "Change the spec for this?",
            (P.Choice(SPECIFY_NOW, "the phase switches to spec; once it is "
                                   "written, Hamilton runs the build"),)),
            NOT_NOW)
        if answer != SPECIFY_NOW:
            return ("The engineer does not want the spec changed for this now. "
                    "Do not work around it in the code: carry on validating "
                    "and ask for their next finding.")
        _phase.write(self.root, "spec")
        return (f"The engineer agreed. The phase is 'spec' now: spec/ is "
                f"writable, code and tests are not. Run the Specify review "
                f"protocol from Phase 1 for this change: {change}\n\nEnd with "
                f"the Phase 4 summary and the {P.SENTINEL} line. Hamilton then "
                f"switches back to build phase, runs the build, and tells you "
                f"how it went.")

    async def after_turn(self, done: bool) -> str | None:
        """What to tell the agent before the engineer goes on, or None."""
        said = ""
        if _phase.read(self.root) == "spec":
            if not done:
                return None             # the review protocol is under way
            _phase.write(self.root, "build")
            said = "The spec change is done and the phase is 'build' again. "
        findings = await asyncio.to_thread(self._gate)
        if not findings:
            if not said:
                return None
            return (said + "It leaves nothing to build. Tell the engineer in a "
                    "line, then ask for their next finding.")
        return said + await self._build()

    async def at_end(self) -> int | None:
        """The engineer has finished: whatever changed since the last build
        is checked with the whole suite."""
        if _phase.read(self.root) == "spec":
            _phase.write(self.root, "build")
        now = _tree.snapshot(self.root)
        if now is not None and now == self.checked:
            return None
        self.console.say()
        self.console.note("The session changed the project since the last "
                          "build: building, to check it with the whole suite.")
        rc, _stopped = await self._run_build()
        return rc

    def _gate(self) -> list:
        return _verify.run(self.root, suite=False)[0]

    async def _run_build(self) -> tuple[int, str]:
        """The build loop, in process. Returns its exit code and why it
        stopped."""
        state = _build.State.load(self.root)
        self.console.start_working(_build.STEPS["check"])
        try:
            rc = await _build.build(self.root, self.worker, self.judge,
                                    self.console, state)
        except Exception as exc:    # a traceback tells the engineer nothing
            self.console.error(f"build: the run stopped on an error -- {exc}")
            rc, state.stopped = 1, str(exc)[:200]
            state.save(self.root)
        finally:
            self.console.stop_working()
        self.checked = _tree.snapshot(self.root)
        return rc, state.stopped

    async def _build(self) -> str:
        rc, stopped = await self._run_build()
        if rc == 0:
            return ("Hamilton ran the build: the gate is green. Tell the "
                    "engineer in a line -- and to restart `hamilton run` if "
                    "the software does not reload by itself -- then ask for "
                    "their next finding.")
        return (f"Hamilton ran the build and it stopped"
                f"{' (' + stopped + ')' if stopped else ''}: the gate is still "
                f"red. Tell the engineer in a line and ask whether to carry on "
                f"validating or to finish and run `hamilton build` again.")


def main() -> int:
    root = os.getcwd()
    console = Console()

    def extras() -> _loop.Extras:
        worker, judge = _build.agents(root)
        return Validation(root, console, worker, judge).extras()

    return _loop.session(root, VALIDATE, console, extras)
