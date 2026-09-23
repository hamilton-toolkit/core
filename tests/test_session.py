"""The session driver -- its checkpoint and turn loop (the console has its own module).

None of this touches the Claude Agent SDK: `loop` is written against
`protocol`, so the driver is exercised with a scripted fake adapter. That the
tests can be written this way at all is the point of the seam -- a future
non-SDK harness is tested the same way.
"""

import asyncio
import contextlib
import io
from pathlib import Path

from conftest import copy_fixture

from hamilton_core.session import loop as L
from hamilton_core.session.console import Console
from hamilton_core.session import protocol as P
from hamilton_core.session.modes import DESIGN, MODES


class FakeAdapter:
    """Answers each message with a scripted list of events, then ends the
    turn, and records what it was sent."""

    def __init__(self, *turns, session_ref=None):
        self._turns = [list(t) for t in turns]
        self._stream = None
        self.session_ref = session_ref
        self.sent = []
        self.closed = False

    async def connect(self):
        self._stream = asyncio.Queue()

    async def send(self, text):
        self.sent.append(text)
        for ev in (self._turns.pop(0) if self._turns else []):
            self._stream.put_nowait(ev)
        self._stream.put_nowait(P.TurnEnded())

    async def events(self):
        while True:
            yield await self._stream.get()

    async def close(self):
        self.closed = True


DONE = P.AgentText(P.SENTINEL)      # the agent's end of an iteration


def console(keys=""):
    """A Console reading `keys` (one answer per line) and capturing output."""
    out = io.StringIO()
    return Console(out=out, inp=io.StringIO(keys), color=False), out


# --- the checkpoint: what makes a budget-exhausted session resumable ---------

def test_checkpoint_round_trips(tmp_path):
    P.Checkpoint("spec", session_ref="abc", turns_completed=3,
                 last_summary="drafted R-0001").save(str(tmp_path))
    cp = P.Checkpoint.load(str(tmp_path))
    assert (cp.phase, cp.session_ref, cp.turns_completed) == ("spec", "abc", 3)
    assert cp.last_summary == "drafted R-0001" and cp.done is False


def test_checkpoint_is_resumable_only_when_unfinished_and_referenced(tmp_path):
    assert P.Checkpoint("spec", session_ref="abc").resumable is True
    assert P.Checkpoint("spec", session_ref="abc", done=True).resumable is False
    assert P.Checkpoint("spec", session_ref=None).resumable is False


def test_missing_or_corrupt_checkpoint_is_simply_nothing_to_resume(tmp_path):
    assert P.Checkpoint.load(str(tmp_path)) is None
    (tmp_path / ".hamilton").mkdir()
    (tmp_path / ".hamilton" / "session").write_text("{not json")
    assert P.Checkpoint.load(str(tmp_path)) is None
    (tmp_path / ".hamilton" / "session").write_text('{"no_phase": 1}')
    assert P.Checkpoint.load(str(tmp_path)) is None


def test_checkpoint_ignores_keys_it_does_not_know(tmp_path):
    (tmp_path / ".hamilton").mkdir()
    (tmp_path / ".hamilton" / "session").write_text(
        '{"phase": "build", "session_ref": "z", "from_a_later_version": 7}')
    cp = P.Checkpoint.load(str(tmp_path))
    assert cp.phase == "build" and cp.session_ref == "z"


# --- the driver -------------------------------------------------------------

def drive(tmp_path, adapter, keys=""):
    c, out = console(keys)
    rc = asyncio.run(L.drive(str(tmp_path), DESIGN, "KICKOFF", adapter, c))
    return rc, out.getvalue(), P.Checkpoint.load(str(tmp_path))


FINISH = "5\n"        # the spec menu's four steps, then "Finish this session"


def test_a_finished_iteration_offers_the_next_step_instead_of_exiting(tmp_path):
    """The agent's summary closes an *iteration*, not the session: ending there
    threw away a conversation the engineer still wanted."""
    a = FakeAdapter([P.AgentText("summary"), DONE],
                    [P.AgentText("on to the next thing")],
                    session_ref="s1")
    rc, out, cp = drive(tmp_path, a, keys="1\n" + "\n")
    assert rc == 0
    # the chosen step was sent as a second turn, in the same session
    assert len(a.sent) == 2 and a.sent[0] == "KICKOFF"
    assert "review protocol" in a.sent[1]
    assert "What next?" in out


def test_finishing_from_the_menu_ends_the_session_as_complete(tmp_path):
    a = FakeAdapter([P.AgentText("summary"), DONE],
                    session_ref="s1")
    rc, out, cp = drive(tmp_path, a, keys=FINISH)
    assert rc == 0
    assert a.sent == ["KICKOFF"]           # nothing more was asked of the agent
    assert cp.done is True and cp.resumable is False
    assert a.closed is True


def test_a_continued_iteration_stays_resumable(tmp_path):
    a = FakeAdapter([DONE], [P.AgentText("working")],
                    session_ref="s1")
    rc, out, cp = drive(tmp_path, a, keys="1\n" + "\n")
    assert cp.done is False and cp.resumable is True


def test_the_next_step_menu_offers_no_typed_answer(tmp_path):
    a = FakeAdapter([DONE], [P.AgentText("ok")], session_ref="s1")
    rc, out, cp = drive(tmp_path, a, keys="split R-0004 into two\n" + "1\n" + "\n")
    assert "type your own" not in out
    assert "review protocol" in a.sent[1]       # the typed text was not sent


def test_each_menu_choice_carries_its_own_instruction(tmp_path):
    for pick, expected in (("1\n", "review protocol"),
                           ("3\n", "decompose"),
                           ("4\n", "add up")):
        a = FakeAdapter([DONE], [P.AgentText("ok")],
                        session_ref="s1")
        drive(tmp_path, a, keys=pick + "\n")
        assert expected in a.sent[1].lower(), (pick, a.sent[1])


class AskingAdapter(FakeAdapter):
    """Asks the engineer one question during the first turn, the way the
    agent does, before the turn's scripted events."""

    def __init__(self, *turns, ask, session_ref=None):
        super().__init__(*turns, session_ref=session_ref)
        self._ask = ask

    async def send(self, text):
        if not self.sent:
            await asyncio.to_thread(self._ask, P.Question(
                "Which parent?", (P.Choice("R-0007"), P.Choice("R-0009"))))
        await super().send(text)


def drive_asking(tmp_path, keys):
    c, out = console(keys)
    a = AskingAdapter([P.AgentText("stopping here")], ask=c.ask, session_ref="s1")
    rc = asyncio.run(L.drive(str(tmp_path), DESIGN, "KICKOFF", a, c))
    return rc, a, P.Checkpoint.load(str(tmp_path))


def test_finishing_at_the_agents_question_ends_the_session_as_complete(tmp_path):
    rc, a, cp = drive_asking(tmp_path, "3\n")    # two choices, then Finish
    assert rc == 0 and a.sent == ["KICKOFF"]
    assert cp.done is True and cp.resumable is False


def test_leaving_the_agents_question_keeps_the_session_resumable(tmp_path):
    rc, a, cp = drive_asking(tmp_path, "")        # EOF at the question
    assert a.sent == ["KICKOFF"]
    assert cp.done is False and cp.resumable is True


def test_an_unfinished_session_checkpoints_as_resumable(tmp_path):
    # one turn, then the engineer ends the session (EOF at the turn prompt)
    a = FakeAdapter([P.AgentText("still working")], session_ref="s2")
    rc, out, cp = drive(tmp_path, a)
    assert rc == 0
    assert cp.done is False and cp.session_ref == "s2"
    assert cp.resumable is True
    assert cp.turns_completed == 1
    assert cp.last_summary == "still working"


def test_the_engineer_can_take_another_turn(tmp_path):
    a = FakeAdapter([P.AgentText("one")], [P.AgentText("two")],
                    session_ref="s3")
    rc, out, cp = drive(tmp_path, a, keys="and now this\n")
    assert a.sent == ["KICKOFF", "and now this"]
    assert cp.turns_completed == 2


def test_a_denied_write_is_shown_to_the_engineer(tmp_path):
    a = FakeAdapter([P.ToolDenied("src/x.py", "phase is 'spec'"),
                     DONE])
    rc, out, cp = drive(tmp_path, a)
    assert "src/x.py" in out and "phase is 'spec'" in out


def test_a_finished_subagent_leaves_a_line(tmp_path):
    a = FakeAdapter([P.TaskStarted("X", "Write test R-0001/AC1"),
                     P.TaskEnded("X", True),
                     P.TaskStarted("Y", "Write test R-0001/AC2"),
                     P.TaskEnded("Y", False), DONE])
    rc, out, cp = drive(tmp_path, a, keys=FINISH)
    assert "✓ Write test R-0001/AC1 (0s)" in out
    assert "✗ Write test R-0001/AC2 failed (0s)" in out


def test_a_session_error_ends_the_session_nonzero(tmp_path):
    a = FakeAdapter([P.SessionError("out of budget")], session_ref="s4")
    rc, out, cp = drive(tmp_path, a)
    assert rc == 1
    assert "out of budget" in out
    # the whole point: an error still leaves something to resume from
    assert cp.resumable is True


def test_the_adapter_is_closed_even_when_a_turn_raises(tmp_path):
    class Boom(FakeAdapter):
        async def send(self, text):
            raise RuntimeError("transport died")

    a = Boom()
    c, _ = console()
    try:
        asyncio.run(L.drive(str(tmp_path), DESIGN, "K", a, c))
    except RuntimeError:
        pass
    assert a.closed is True


# --- changing specific requirements ---------------------------------------------

def test_picked_requirements_and_the_change_are_sent_to_the_agent(tmp_path):
    root = copy_fixture("tree", tmp_path)
    a = FakeAdapter([DONE], [P.AgentText("ok")], session_ref="s1")
    c, out = console("2\n" + "3\n" + "let tokens expire after an hour\n" + "\n")
    asyncio.run(L.drive(root, DESIGN, "KICKOFF", a, c))
    assert 'R-0042 "Reject expired tokens"' in a.sent[1]
    assert "let tokens expire after an hour" in a.sent[1]
    assert "{requirements}" not in a.sent[1]


def test_the_tree_is_offered_indented_by_depth(tmp_path):
    root = copy_fixture("tree", tmp_path)
    a = FakeAdapter([DONE], [P.AgentText("ok")], session_ref="s1")
    c, out = console("2\n" + "1\n" + "x\n" + "\n")
    asyncio.run(L.drive(root, DESIGN, "KICKOFF", a, c))
    assert '  1) R-0001 "Authentication"' in out.getvalue()
    assert '  3)     R-0042 "Reject expired tokens"' in out.getvalue()


def test_backing_out_of_the_pick_shows_the_menu_again(tmp_path):
    root = copy_fixture("tree", tmp_path)
    a = FakeAdapter([DONE], [P.AgentText("ok")], session_ref="s1")
    c, out = console("2\n" + "\n" + "1\n" + "\n")      # back out, then step 1
    asyncio.run(L.drive(root, DESIGN, "KICKOFF", a, c))
    assert out.getvalue().count("Iteration complete") == 2
    assert "review protocol" in a.sent[1]


def test_an_empty_tree_has_nothing_to_pick(tmp_path):
    a = FakeAdapter([DONE], [P.AgentText("ok")], session_ref="s1")
    rc, out, cp = drive(tmp_path, a, keys="2\n" + "1\n" + "\n")
    assert "nothing to pick" in out
    assert "review protocol" in a.sent[1]


# --- modes --------------------------------------------------------------------

def test_every_mode_is_a_cli_command_and_complete():
    from hamilton_core import cli
    for name, mode in MODES.items():
        assert mode.name == name
        assert mode.phase in ("spec", "build")
        assert mode.kickoff and mode.footer and mode.help and mode.next_steps
        for step in mode.next_steps:
            assert step.label and step.instruction
            if step.picks_requirements:
                assert "{requirements}" in step.instruction
                assert "{change}" in step.instruction
    parser_help = io.StringIO()
    with contextlib.redirect_stdout(parser_help):
        try:
            cli.main(["--help"])
        except SystemExit:
            pass
    for name in MODES:
        assert name in parser_help.getvalue()


def test_reverse_refuses_a_spec_that_already_has_content(tmp_path):
    reverse = MODES["reverse"]
    assert reverse.precheck(str(tmp_path)) is None     # no spec yet: fine
    populated = Path(__file__).parent / "fixtures" / "clean"
    assert "already has" in reverse.precheck(str(populated))


class Spending(FakeAdapter):
    """Ends each turn having used `per_turn` tokens."""

    def __init__(self, *turns, per_turn=12_300, **kw):
        super().__init__(*turns, **kw)
        self.per_turn = per_turn

    async def send(self, text):
        self.sent.append(text)
        for ev in (self._turns.pop(0) if self._turns else []):
            self._stream.put_nowait(ev)
        self._stream.put_nowait(P.TurnEnded(tokens=self.per_turn))


def test_each_turn_shows_its_tokens_and_the_session_its_total(tmp_path):
    a = Spending([P.AgentText("summary"), DONE],
                 [P.AgentText("on to the next thing"), DONE], session_ref="s1")
    rc, out, _cp = drive(tmp_path, a, keys="1\n" + "\n" + FINISH)
    assert "12.3k tokens · 12.3k this session" in out
    assert "12.3k tokens · 24.6k this session" in out
    assert out.rstrip().endswith("Tokens 24.6k this session")


def test_a_session_that_used_nothing_shows_no_tokens(tmp_path):
    rc, out, _cp = drive(tmp_path, FakeAdapter([P.AgentText("summary"), DONE]), keys=FINISH)
    assert "tokens" not in out.lower()
