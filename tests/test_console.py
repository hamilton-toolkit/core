"""The console -- styling, the cursor picker, the input editor, the no-TTY fallback.

The editor and picker are `prompt_toolkit`; they are driven here through a pipe
input, so keypresses go in and answers come out without a real terminal.
"""

import io
import threading
import time

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from hamilton_core.session import console as C
from hamilton_core.session import protocol as P


def console(keys="", color=False):
    out = io.StringIO()
    return C.Console(out=out, inp=io.StringIO(keys), color=color), out


QUESTION = P.Question("Which parent?", (P.Choice("R-0007", "Sessions"),
                                        P.Choice("R-0009", "Tokens")))


# --- the no-TTY fallback: one line, no second confirmation -------------------

def test_a_numbered_choice_is_returned_by_label():
    c, _ = console("1\n")
    assert c.ask(QUESTION) == "R-0007"


def test_answering_takes_a_single_line():
    # the whole point of the rewrite: no "are you sure?" round trip
    c, _ = console("2\n")
    assert c.ask(QUESTION) == "R-0009"


def test_a_number_off_the_list_re_asks():
    c, out = console("9\n1\n")
    assert c.ask(QUESTION) == "R-0007"
    assert "not a choice on the list" in out.getvalue()


def test_a_typed_answer_is_taken_as_free_text():
    c, _ = console("neither, put it under R-0011\n")
    assert c.ask(QUESTION) == "neither, put it under R-0011"


def test_an_open_question_takes_free_text():
    c, _ = console("returns an empty string\n")
    assert c.ask(P.Question("What should initials('') return?")) == \
        "returns an empty string"


def test_eof_at_a_question_aborts_the_session():
    c, _ = console("")
    c.ask(QUESTION)
    assert c.aborted is True


def test_the_resume_offer_shows_where_the_session_had_got_to():
    c, out = console("y\n")
    cp = P.Checkpoint("spec", session_ref="s", turns_completed=4,
                      last_summary="ratified R-0003")
    assert c.offer_resume(cp) is True
    assert "4 turn(s) in" in out.getvalue()
    assert "ratified R-0003" in out.getvalue()


def test_declining_or_ending_the_resume_offer_starts_fresh():
    assert console("n\n")[0].offer_resume(P.Checkpoint("spec", session_ref="s")) is False
    assert console("")[0].offer_resume(P.Checkpoint("spec", session_ref="s")) is False


# --- styling -----------------------------------------------------------------

def test_paint_is_a_pass_through_when_disabled():
    off = C.Paint(False)
    assert off.bold("x") == "x" and off.red("x") == "x"


def test_paint_wraps_and_always_resets():
    on = C.Paint(True)
    assert on.bold("x").startswith("\x1b[1m") and on.bold("x").endswith("\x1b[0m")


def test_colour_is_off_without_a_tty_or_under_no_color(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert C.supports_color(io.StringIO()) is False
    monkeypatch.setenv("NO_COLOR", "1")

    class FakeTTY(io.StringIO):
        def isatty(self): return True

    assert C.supports_color(FakeTTY()) is False
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("TERM", "dumb")
    assert C.supports_color(FakeTTY()) is False


def test_inline_markdown_becomes_ansi():
    paint = C.Paint(True)
    assert "\x1b[1m" in C.markdown("a **bold** word", paint)
    assert "\x1b[36m" in C.markdown("run `hamilton check`", paint)
    assert C.markdown("## Heading", paint) == paint.bold("Heading")


def test_markdown_leaves_plain_prose_alone():
    assert C.markdown("just prose", C.Paint(False)) == "just prose"


# --- the working indicator ---------------------------------------------------

def test_the_indicator_stays_silent_without_a_terminal():
    c, out = console()
    c.start_working()
    assert c.working is False          # nothing to animate over a pipe
    c.stop_working()
    assert out.getvalue() == ""


def test_stopping_an_indicator_that_never_started_is_harmless():
    c, _ = console()
    c.stop_working()


def test_elapsed_reads_as_time():
    assert C.elapsed(3) == "3s"
    assert C.elapsed(59) == "59s"
    assert C.elapsed(60) == "1m00s"
    assert C.elapsed(135) == "2m15s"


def test_the_indicator_is_suspended_around_anything_that_reads():
    """The agent asks its questions from a worker thread while the indicator is
    running; if `ask` did not suspend it, the animation would overwrite the
    picker mid-draw."""
    c, _ = console("1\n")
    seen = []
    c._interactive = lambda: False
    original = c._paused

    import contextlib

    @contextlib.contextmanager
    def watched():
        seen.append(True)
        with original():
            yield

    c._paused = watched
    c.ask(QUESTION)
    assert seen == [True]


# --- finishing gracefully ----------------------------------------------------

def test_a_question_offers_a_way_to_finish():
    """So a session started by accident can be left at its very first
    question, rather than needing Ctrl+C."""
    c, out = console("3\n")          # two choices, then "Finish this session"
    assert c.ask(QUESTION) == C.ENDED
    assert c.aborted is True
    assert "Finish this session" in out.getvalue()


def test_the_fixed_rows_carry_no_icons():
    assert C.OTHER == "Type my own answer"
    assert C.FINISH_ROW == "Finish this session"


def test_finishing_a_question_reads_as_an_interruption_not_completion():
    # mid-question the engineer is leaving mid-thought: the driver treats
    # `aborted` as resumable, which is what we want here
    c, _ = console("3\n")
    c.ask(QUESTION)
    assert c.aborted is True


def test_choose_returns_none_when_the_engineer_finishes():
    c, _ = console("3\n")
    assert c.choose(QUESTION) is None
    assert c.aborted is False        # a menu finish is completion, not an abort


def test_choose_returns_the_chosen_label():
    c, _ = console("2\n")
    assert c.choose(QUESTION) == "R-0009"


def test_choose_takes_no_typed_answer():
    c, out = console("something else entirely\n" + "2\n")
    assert c.choose(QUESTION) == "R-0009"
    assert "type your own answer" not in out.getvalue()


def test_ending_input_at_a_menu_is_also_a_finish():
    c, _ = console("")
    assert c.choose(QUESTION) is None


# --- the terminal editor and picker (prompt_toolkit) -------------------------

@pytest.fixture
def tty():
    """Build a console whose terminal input is a pipe. Each script is a list of
    key strings, one per prompt, sent once that prompt is listening: keys left
    over when one prompt exits would not reach the next."""
    pipes = []

    def build(*scripts):
        ctx = create_pipe_input()
        pipe = ctx.__enter__()
        pipes.append(ctx)
        pending = list(scripts)
        out = io.StringIO()

        def session():
            keys = pending.pop(0)
            # sent from a thread, after the prompt has started reading
            threading.Thread(target=lambda: (time.sleep(0.05),
                                             pipe.send_text(keys))).start()
            return create_app_session(input=pipe, output=DummyOutput())

        return C.Console(out=out, inp=io.StringIO(), color=False,
                         terminal=session), out

    yield build
    for ctx in pipes:
        ctx.__exit__(None, None, None)


def test_enter_sends_and_alt_enter_or_ctrl_j_add_a_line(tty):
    c, _ = tty("one\x1b\rtwo\nthree\r")
    assert c.ask(P.Question("Why?")) == "one\ntwo\nthree"


def test_a_pasted_paragraph_arrives_whole_instead_of_sending(tty):
    c, _ = tty("\x1b[200~line one\rline two\x1b[201~\r")
    assert c.ask(P.Question("Why?")) == "line one\nline two"


def test_the_sent_answer_is_reprinted_as_plain_text(tty):
    c, out = tty("one\x1b\rtwo\r")
    c.ask(P.Question("Why?"))
    assert "> one\n  two\n" in out.getvalue()


def test_the_reprint_adds_no_line_breaks_of_its_own():
    # the terminal wraps it, so a copy from the scrollback is the text as typed
    assert C.echo("x" * 300) == "> " + "x" * 300


def test_ending_input_in_the_editor_ends_the_session(tty):
    c, _ = tty("\x04")
    assert c.ask(P.Question("Why?")) == C.ENDED
    assert c.aborted is True


def test_the_picker_returns_the_highlighted_choice(tty):
    c, out = tty("\x1b[B\r")
    assert c.choose(QUESTION) == "R-0009"
    assert "  R-0009" in out.getvalue()


def test_tab_moves_the_highlight_like_the_arrows(tty):
    c, _ = tty("\t\x1b[Z\t\r")
    assert c.choose(QUESTION) == "R-0009"


def test_picking_type_my_own_answer_opens_the_editor(tty):
    c, _ = tty("\t\t\r", "in my own words\r")
    assert c.ask(QUESTION) == "in my own words"


def test_hamiltons_own_menu_has_no_type_my_own_answer_row(tty):
    c, _ = tty("\t\t\r")                     # third row is Finish here
    assert c.choose(QUESTION) is None


def test_picking_finish_ends_the_session(tty):
    c, _ = tty("\x1b[B" * 3 + "\r")
    assert c.ask(QUESTION) == C.ENDED
    assert c.aborted is True


# --- choices that duplicate the fixed rows ------------------------------------

def test_catch_all_and_exit_choices_are_dropped_since_hamilton_adds_its_own():
    q = P.Question("Exit, or another change?", (
        P.Choice("Exit session"), P.Choice("Another change"),
        P.Choice("Something else — let me explain"), P.Choice("Other"),
        P.Choice("Finish this session"), P.Choice("End the session")))
    assert C.Console._without_fixed_rows(q).choices == (P.Choice("Another change"),)


def test_a_real_choice_that_merely_mentions_those_words_is_kept():
    q = P.Question("p", (P.Choice("Use the other repo"),
                         P.Choice("Finish the vision first"),
                         P.Choice("Exitcode handling")))
    assert C.Console._without_fixed_rows(q).choices == q.choices


def test_the_agents_exit_choice_never_reaches_the_numbered_list():
    c, out = console("\n")
    c.ask(P.Question("Exit, or another change?",
                     (P.Choice("Exit session"), P.Choice("Another change"))))
    assert "Exit session" not in out.getvalue()


# --- selecting several rows ------------------------------------------------------

OPTIONS = [("R-0001", 'R-0001 "Authentication"'),
           ("R-0007", '  R-0007 "Sessions"'),
           ("R-0042", '    R-0042 "Reject expired tokens"')]


def test_ticked_rows_are_returned_and_reprinted(tty):
    c, out = tty(" \x1b[B\x1b[B \r")
    assert c.select("Which?", OPTIONS) == ["R-0001", "R-0042"]
    assert 'R-0042 "Reject expired tokens"' in out.getvalue()


def test_enter_with_nothing_ticked_takes_the_highlighted_row(tty):
    c, _ = tty("\x1b[B\r")
    assert c.select("Which?", OPTIONS) == ["R-0007"]


def test_leaving_the_selection_goes_back(tty):
    c, _ = tty("\x04")
    assert c.select("Which?", OPTIONS) is None
    assert c.aborted is False


def test_numbered_selection_takes_comma_separated_numbers():
    c, out = console("1,4\n" + "1, 3\n")
    assert c.select("Which?", OPTIONS) == ["R-0001", "R-0042"]
    assert "not numbers on the list" in out.getvalue()


def test_an_empty_numbered_selection_goes_back():
    c, _ = console("\n")
    assert c.select("Which?", OPTIONS) is None


def test_an_empty_text_answer_goes_back_without_ending_the_session():
    c, _ = console("\n")
    assert c.text("What should change?") is None
    assert c.aborted is False
