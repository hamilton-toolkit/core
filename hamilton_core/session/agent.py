"""The agent session as Hamilton drives it, over any `protocol.AgentAdapter`.

An adapter only translates a vendor's stream. What a turn is, which subagents
are running and when a phase is done are decided here, once, whatever model
answers:

  * **The stream is always read.** A pump task reads the adapter's events from
    the first turn to the end of the session -- also while the engineer is
    typing. A vendor session nobody reads can stall, and then its permission
    requests and questions go unanswered; so what the agent does between turns
    is taken in as it happens, and shown at the start of the next turn.
  * **A turn ends on a `TurnEnded` with no subagent still running.** A vendor
    may hand the agent its subagent back the moment it is launched and end the
    turn while the work goes on; the engineer would get their prompt back over
    a session that is still writing files, and the agent's own report would
    land in the middle of whatever they typed. So the turn is held open until
    every row has closed and the agent has finished saying what came of it. A
    lull after the last one ends the wait, in case nothing follows.
  * **Subagents are rows**: `TaskStarted` opens one, `TaskProgress` updates
    it, `TaskEnded` closes it into a `SubagentDone`. `activity()` is what the
    console draws while the turn runs.
  * **The completion sentinel** is taken out of the agent's text and becomes a
    `PhaseDone` at the end of the turn. The engineer reads the closing
    summary, not the marker that ends it.
  * **What a turn cost** is a `Spent` at its end: the tokens of every
    `TurnEnded` it took in, turns the agent started itself included.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import time
from typing import AsyncIterator

from hamilton_core.session import protocol as P

# How long to wait, after the last subagent has reported, for the agent to say
# what came of it. It answers within a breath or not at all.
QUIET = 30.0

# ... and how long a silence while one is still running before we take it as
# lost and hand the engineer back their prompt. A subagent that is alive
# reports progress, so this is a stall guard, not a deadline on the work.
STALLED = 300.0


class Agent:
    def __init__(self, adapter: P.AgentAdapter) -> None:
        self._adapter = adapter
        self._queue: asyncio.Queue = asyncio.Queue()   # unbounded: never blocks the pump
        self._rows: dict[str, P.Activity] = {}
        self._snapshot: tuple[P.Activity, ...] = ()
        self._pump_task: asyncio.Task | None = None
        self._spent = 0                 # tokens of the turn running now

    @property
    def session_ref(self) -> str | None:
        return self._adapter.session_ref

    def activity(self) -> tuple[P.Activity, ...]:
        """The subagents running now, oldest first. Safe to call from another
        thread (the console's indicator does): it returns a snapshot taken
        after each change, never the live table."""
        return self._snapshot

    async def run_turn(self, text: str) -> AsyncIterator[P.Event]:
        done, tail = False, ""
        self._spent = 0
        async for ev in self._turn(text):
            if isinstance(ev, P.AgentText):
                body, hit = strip_sentinel(ev.text)
                done = done or hit
                if not body.strip():
                    continue
                tail, ev = body, P.AgentText(body)
            yield ev
        if self._spent:
            yield P.Spent(self._spent)
        if done:
            yield P.PhaseDone(tail.strip())

    async def close(self) -> None:
        if self._pump_task is not None:
            self._pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump_task
        await self._adapter.close()

    async def _turn(self, text: str) -> AsyncIterator[P.Event]:
        """What arrived since the last turn, then this turn's events."""
        if self._pump_task is None:
            await self._adapter.connect()
            self._pump_task = asyncio.create_task(self._pump())
        while not self._queue.empty():
            ev = self._queue.get_nowait()
            if isinstance(ev, P.TurnEnded):         # a turn the agent started
                self._spent += ev.tokens
            else:
                yield ev
        if self._pump_task.done():
            return                                  # the stream is gone
        await self._adapter.send(text)
        ended = False
        while (ev := await self._next(ended)) is not None:
            if isinstance(ev, P.TurnEnded):
                ended = True
                self._spent += ev.tokens
                if not self._rows:
                    return
                continue                            # subagents still running
            yield ev

    async def _next(self, ended: bool):
        """The next event, or None when the turn is over. Once the turn has
        ended, silence is what ends it: a short one when the subagents have
        all reported and nothing followed, a long one when a subagent has
        stopped saying anything at all. Either way the engineer gets their
        prompt back rather than a session that never speaks again."""
        if not ended:
            return await self._queue.get()
        try:
            return await asyncio.wait_for(
                self._queue.get(), STALLED if self._rows else QUIET)
        except asyncio.TimeoutError:
            return None

    async def _pump(self) -> None:
        try:
            async for ev in self._adapter.events():
                self._take(ev)
            problem = "the agent session ended unexpectedly"
        except Exception as e:
            problem = f"the agent session failed: {e}"
        self._queue.put_nowait(P.SessionError(problem))
        self._queue.put_nowait(P.TurnEnded())       # so a waiting turn ends

    def _take(self, ev: P.StreamEvent) -> None:
        if isinstance(ev, P.TaskStarted):
            self._rows[ev.id] = P.Activity(ev.id, ev.label, time.monotonic())
        elif isinstance(ev, P.TaskProgress):
            if ev.id in self._rows:
                self._rows[ev.id] = dataclasses.replace(
                    self._rows[ev.id], doing=ev.doing)
        elif isinstance(ev, P.TaskEnded):
            row = self._rows.pop(ev.id, None)
            if row is not None:
                self._queue.put_nowait(
                    P.SubagentDone(row.label, ev.ok, time.monotonic() - row.started))
        else:
            self._queue.put_nowait(ev)
            return
        self._snapshot = tuple(self._rows.values())


def strip_sentinel(text: str) -> tuple[str, bool]:
    """Text with the sentinel line removed, and whether it was there."""
    if P.SENTINEL not in text:
        return text, False
    kept = [ln for ln in text.splitlines() if ln.strip() != P.SENTINEL]
    return "\n".join(kept), True
