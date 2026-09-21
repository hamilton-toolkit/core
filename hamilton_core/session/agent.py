"""The agent session as Hamilton drives it, over any `protocol.AgentAdapter`.

An adapter only translates a vendor's stream. What a turn is, which subagents
are running and when a phase is done are decided here, once, whatever model
answers:

  * **The stream is always read.** A pump task reads the adapter's events from
    the first turn to the end of the session -- also while the engineer is
    typing. A vendor session nobody reads can stall, and then its permission
    requests and questions go unanswered; so what the agent does between turns
    is taken in as it happens, and shown at the start of the next turn.
  * **A turn ends on `TurnEnded`**, which an adapter sends only for the turn
    the engineer's message started -- not for one the agent started itself.
  * **Subagents are rows**: `TaskStarted` opens one, `TaskProgress` updates
    it, `TaskEnded` closes it into a `SubagentDone`. `activity()` is what the
    console draws while the turn runs.
  * **The completion sentinel** is taken out of the agent's text and becomes a
    `PhaseDone` at the end of the turn.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import time
from typing import AsyncIterator

from hamilton_core.session import protocol as P


class Agent:
    def __init__(self, adapter: P.AgentAdapter) -> None:
        self._adapter = adapter
        self._queue: asyncio.Queue = asyncio.Queue()   # unbounded: never blocks the pump
        self._rows: dict[str, P.Activity] = {}
        self._snapshot: tuple[P.Activity, ...] = ()
        self._pump_task: asyncio.Task | None = None

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
        async for ev in self._turn(text):
            if isinstance(ev, P.AgentText):
                body, hit = strip_sentinel(ev.text)
                done = done or hit
                if not body.strip():
                    continue
                tail, ev = body, P.AgentText(body)
            yield ev
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
            if not isinstance(ev, P.TurnEnded):     # a turn the agent started
                yield ev
        if self._pump_task.done():
            return                                  # the stream is gone
        await self._adapter.send(text)
        while not isinstance(ev := await self._queue.get(), P.TurnEnded):
            yield ev

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
                    self._rows[ev.id], tool_uses=ev.tool_uses, last_tool=ev.last_tool)
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
    """Text with the sentinel line removed, and whether it was there. The
    engineer should see the closing summary, not the marker that ends it."""
    if P.SENTINEL not in text:
        return text, False
    kept = [ln for ln in text.splitlines() if ln.strip() != P.SENTINEL]
    return "\n".join(kept), True
