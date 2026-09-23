"""`Agent` -- turns, subagent rows and the completion sentinel, over any
adapter. Driven by a scripted adapter: none of this is vendor-specific.
"""

import asyncio

from hamilton_core.session import protocol as P
from hamilton_core.session.agent import Agent, strip_sentinel


class ScriptedAdapter:
    """Emits what the test puts in `stream`, and a scripted reply per `send`."""

    def __init__(self, *replies):
        self.replies = [list(r) for r in replies]
        self.stream: asyncio.Queue = asyncio.Queue()
        self.session_ref = "s1"
        self.sent = []
        self.closed = False

    async def connect(self):
        pass

    async def send(self, text):
        self.sent.append(text)
        for ev in (self.replies.pop(0) if self.replies else []):
            self.stream.put_nowait(ev)
        self.stream.put_nowait(P.TurnEnded())

    async def events(self):
        while (ev := await self.stream.get()) is not None:
            yield ev

    async def close(self):
        self.closed = True


async def turn(agent, text="go"):
    return [ev async for ev in agent.run_turn(text)]


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 5))


def test_a_turn_yields_its_events_up_to_its_end():
    async def go():
        a = Agent(ScriptedAdapter([P.AgentText("one"), P.AgentText("two")]))
        return await turn(a)
    assert run(go()) == [P.AgentText("one"), P.AgentText("two")]


def test_a_subagent_is_a_row_while_it_runs_and_a_done_line_after():
    async def go():
        a = Agent(ScriptedAdapter(
            [P.TaskStarted("X", "Write test R-0001/AC1"), P.TaskProgress("X", "editing a file"),
             P.AgentText("launched")],
            [P.TaskEnded("X", True), P.AgentText("written")]))
        first = await turn(a)
        rows = a.activity()
        return first, rows, await turn(a, "next"), a.activity()

    first, rows, second, after = run(go())
    assert first == [P.AgentText("launched")]
    [row] = rows
    assert (row.label, row.doing) == ("Write test R-0001/AC1", "editing a file")
    done, written = second
    assert isinstance(done, P.SubagentDone) and done.label == "Write test R-0001/AC1"
    assert done.ok is True and written == P.AgentText("written")
    assert after == ()


def test_the_end_of_a_tool_that_was_never_a_task_is_ignored():
    async def go():
        a = Agent(ScriptedAdapter([P.TaskProgress("b1", "running a command"),
                                   P.TaskEnded("b1", True), P.AgentText("ok")]))
        return await turn(a)
    assert run(go()) == [P.AgentText("ok")]


def test_what_the_agent_says_between_turns_opens_the_next_one():
    async def go():
        adapter = ScriptedAdapter([P.AgentText("first")], [P.AgentText("second")])
        a = Agent(adapter)
        first = await turn(a)
        # a turn the agent started itself: its text, and an end that is not ours
        adapter.stream.put_nowait(P.AgentText("the background task finished"))
        adapter.stream.put_nowait(P.TurnEnded())
        await asyncio.sleep(0)
        return first, await turn(a, "next")
    first, second = run(go())
    assert first == [P.AgentText("first")]
    assert second == [P.AgentText("the background task finished"), P.AgentText("second")]


def test_the_stream_is_read_while_no_turn_runs():
    """The klimasofort hang: nothing read the vendor stream between turns, it
    filled up, and permission requests behind it were never answered."""
    async def go():
        adapter = ScriptedAdapter([P.AgentText("first")], [])
        a = Agent(adapter)
        await turn(a)
        for i in range(150):
            adapter.stream.put_nowait(P.AgentText(f"note {i}"))
        for _ in range(10):
            await asyncio.sleep(0)
        drained = adapter.stream.empty()
        return drained, await turn(a, "next")
    drained, second = run(go())
    assert drained is True
    assert len(second) == 150


def test_the_sentinel_ends_the_phase_and_is_hidden():
    async def go():
        a = Agent(ScriptedAdapter([P.AgentText(f"Spec is ratified.\n{P.SENTINEL}")]))
        return await turn(a)
    assert run(go()) == [P.AgentText("Spec is ratified."), P.PhaseDone("Spec is ratified.")]


def test_a_stream_that_breaks_ends_the_turn_with_an_error():
    class Broken(ScriptedAdapter):
        async def events(self):
            await self.stream.get()
            raise ConnectionError("CLI exited")
            yield  # pragma: no cover

    async def go():
        a = Agent(Broken())
        first = await turn(a)
        return first, await turn(a, "again")
    first, again = run(go())
    assert first == [P.SessionError("the agent session failed: CLI exited")]
    assert again == []


def test_close_stops_reading_and_closes_the_adapter():
    async def go():
        adapter = ScriptedAdapter([P.AgentText("x")])
        a = Agent(adapter)
        await turn(a)
        await a.close()
        return adapter, a._pump_task
    adapter, pump = run(go())
    assert adapter.closed is True and pump.done()


def test_strip_sentinel():
    body, hit = strip_sentinel(f"Spec is ratified.\n{P.SENTINEL}")
    assert hit is True and body.strip() == "Spec is ratified."
    assert strip_sentinel("plain text") == ("plain text", False)


# --- a turn that waits for its subagents -------------------------------------

def test_the_turn_waits_for_a_subagent_the_vendor_returned_early(monkeypatch):
    """A vendor may end the turn as soon as a subagent is launched. If Hamilton
    ended it there, the engineer would get their prompt back over a session
    still writing files, and the agent's report would land on top of whatever
    they were typing."""
    monkeypatch.setattr("hamilton_core.session.agent.STALLED", 5.0)

    async def go():
        adapter = ScriptedAdapter([P.TaskStarted("t1", "Write test"),
                                   P.AgentText("launched")])
        a = Agent(adapter)
        seen = []

        async def collect():
            async for ev in a.run_turn("go"):
                seen.append(ev)

        reading = asyncio.create_task(collect())
        await asyncio.sleep(0.1)       # the turn has ended; the subagent has not
        held = not reading.done()
        for ev in (P.TaskEnded("t1", True), P.AgentText("the writer is done"),
                   P.TurnEnded(by_agent=True)):
            adapter.stream.put_nowait(ev)
        await asyncio.wait_for(reading, 2)
        return held, seen

    held, seen = run(go())
    assert held is True
    assert seen[0] == P.AgentText("launched")
    assert isinstance(seen[1], P.SubagentDone) and seen[1].label == "Write test"
    assert seen[2] == P.AgentText("the writer is done")


def test_a_subagent_that_stops_reporting_does_not_strand_the_engineer():
    """Bounded, so a lost subagent costs a wait, not the session."""
    async def go():
        a = Agent(ScriptedAdapter([P.TaskStarted("t1", "Write test"),
                                   P.AgentText("launched")]))
        return await turn(a), a.activity()
    seen, still_running = run(go())      # STALLED is a blink in the tests
    assert seen == [P.AgentText("launched")]
    assert [r.label for r in still_running] == ["Write test"]


def test_a_turn_ends_with_what_it_spent_turns_the_agent_started_included():
    async def go():
        adapter = ScriptedAdapter([P.AgentText("first")], [P.AgentText("second")])
        a = Agent(adapter)
        first = await turn(a)
        # between turns, the agent reports a finished task in a turn of its own
        adapter.stream.put_nowait(P.TurnEnded(by_agent=True, tokens=500))
        await asyncio.sleep(0)
        adapter.replies[0].append(P.TurnEnded(tokens=1_000))
        return first, await turn(a, "next")

    first, second = run(go())
    assert first == [P.AgentText("first")]              # a turn that spent nothing says so
    assert second == [P.AgentText("second"), P.Spent(1_500)]
