"""The console -- styling, the cursor picker, the input editor, the no-TTY fallback.

The picker's control flow (`select_loop`) and its key decoding (`read_key`)
take their terminal I/O as arguments, so the real logic is tested here without
a pty: a scripted list of keypresses in, a chosen index out.
"""

import io
import os
import re

from hamilton_core.session import console as C
from hamilton_core.session import protocol as P


def console(keys="", color=False):
    out = io.StringIO()
    return C.Console(out=out, inp=io.StringIO(keys), color=color), out


QUESTION = P.Question("Which parent?", (P.Choice("R-0007", "Sessions"),
                                        P.Choice("R-0009", "Tokens")))


# --- key decoding ------------------------------------------------------------

def reader(data: bytes):
    """A read(n) over a scripted byte string."""
    buf = bytearray(data)

    def read(n):
        taken = bytes(buf[:n])
        del buf[:n]
        return taken
    return read


def test_arrow_keys_and_enter_are_named():
    assert C.read_key(reader(b"\x1b[A")) == "up"
    assert C.read_key(reader(b"\x1b[B")) == "down"
    assert C.read_key(reader(b"\r")) == "enter"
    assert C.read_key(reader(b"\n")) == "enter"


def test_tab_moves_down_and_shift_tab_moves_up():
    assert C.read_key(reader(b"\t")) == "down"
    assert C.read_key(reader(b"\x1b[Z")) == "up"


def test_interrupt_and_end_of_input_are_named():
    assert C.read_key(reader(b"\x03")) == "interrupt"
    assert C.read_key(reader(b"\x04")) == "eof"
    assert C.read_key(reader(b"")) == "eof"
    assert C.read_key(reader(b"\x1b")) == "esc"


# --- the multi-line edit buffer ---------------------------------------------

def typed(script, buf="", pos=0):
    """Fold a script of (key, char) pairs through the edit rules."""
    for key, ch in script:
        buf, pos = C.edit_keys(buf, pos, key, ch)
    return buf, pos


def chars(text):
    return [("char", c) for c in text]


def test_typing_accumulates():
    assert typed(chars("ada")) == ("ada", 3)


def test_alt_enter_opens_a_new_line_rather_than_sending():
    buf, pos = typed(chars("first") + [("newline", "")] + chars("second"))
    assert buf == "first\nsecond"
    assert pos == len(buf)


def test_several_paragraphs_are_possible():
    buf, _ = typed(chars("a") + [("newline", "")] * 2 + chars("b"))
    assert buf == "a\n\nb"


def test_backspace_deletes_before_the_cursor_and_stops_at_the_start():
    assert typed(chars("ab") + [("backspace", "")]) == ("a", 1)
    assert typed([("backspace", "")]) == ("", 0)


def test_the_cursor_moves_and_inserts_in_the_middle():
    buf, pos = typed(chars("ac") + [("left", "")] + chars("b"))
    assert buf == "abc" and pos == 2


def test_cursor_movement_is_bounded():
    assert typed(chars("ab") + [("left", "")] * 5)[1] == 0
    assert typed(chars("ab") + [("home", "")] + [("right", "")] * 9)[1] == 2


def test_home_and_end_jump_to_the_ends():
    assert typed(chars("abc") + [("home", "")])[1] == 0
    assert typed(chars("abc") + [("home", ""), ("end", "")])[1] == 3


def test_ctrl_u_clears_the_buffer():
    assert typed(chars("throw this away") + [("kill", "")]) == ("", 0)


def test_an_unknown_key_changes_nothing():
    assert typed(chars("ab") + [("other", "")]) == ("ab", 2)


def test_edit_keys_decode_alt_enter_distinctly_from_escape_and_arrows():
    # the three sequences that all begin with Esc must not be confused
    r, w = os.pipe()
    os.write(w, b"\x1b\r\x1b[D\x1b[A")
    assert C.read_edit_key(r)[0] == "newline"
    assert C.read_edit_key(r)[0] == "left"
    assert C.read_edit_key(r)[0] == "up"
    os.close(w)
    assert C.read_edit_key(r)[0] == "eof"
    os.close(r)


def test_plain_keys_decode():
    r, w = os.pipe()
    os.write(w, b"a\r\n\x7f\x03\x04\x15")
    assert C.read_edit_key(r) == ("char", "a")
    assert C.read_edit_key(r)[0] == "submit"
    assert C.read_edit_key(r)[0] == "newline"     # Ctrl+J fallback
    for expected in ("backspace", "interrupt", "eof", "kill"):
        assert C.read_edit_key(r)[0] == expected
    os.close(w)
    os.close(r)


def test_multibyte_characters_survive():
    r, w = os.pipe()
    os.write(w, "ü€".encode())
    assert C.read_edit_key(r) == ("char", "ü")
    assert C.read_edit_key(r) == ("char", "€")
    os.close(w)
    os.close(r)


# --- layout arithmetic behind the editor's redraw ---------------------------

def row_texts(buf, width):
    return [buf[s:e] for s, e in C.text_rows(buf, width)]


def test_wrapping_honours_explicit_newlines():
    assert row_texts("ab\ncd", 10) == ["ab", "cd"]
    assert row_texts("a\n\nb", 10) == ["a", "", "b"]


def test_wrapping_splits_at_the_width():
    assert row_texts("abcde", 2) == ["ab", "cd", "e"]


def test_a_row_that_fills_leaves_a_row_for_the_caret():
    assert row_texts("abcd", 2) == ["ab", "cd", ""]
    assert row_texts("", 2) == [""]


def test_the_cursor_lands_on_the_next_row_when_a_row_fills():
    assert C.caret_rc("ab", 2, 2) == (1, 0)
    assert C.caret_rc("abc", 3, 2) == (1, 1)
    assert C.caret_rc("a\nb", 3, 10) == (1, 1)
    assert C.caret_rc("abc", 1, 2) == (0, 1)     # mid-text, not only at the end


# --- moving up and down -------------------------------------------------------

def test_up_and_down_keep_the_column():
    buf = "hello\nworld"
    pos, _ = C.move_vertical(buf, 9, 20, -1, None)       # "wor|ld" -> "hel|lo"
    assert pos == 3
    pos, _ = C.move_vertical(buf, pos, 20, 1, None)
    assert pos == 9


def test_a_short_row_clamps_but_the_goal_column_survives():
    buf = "a long line\nab\nanother line"
    pos, goal = C.move_vertical(buf, 8, 20, 1, None)     # col 8 -> end of "ab"
    assert pos == buf.index("ab") + 2 and goal == 8
    pos, goal = C.move_vertical(buf, pos, 20, 1, goal)   # back out to col 8
    assert pos == buf.index("another") + 8


def test_up_and_down_move_across_wrapped_rows_too():
    assert C.move_vertical("abcdef", 4, 3, -1, None)[0] == 1


def test_up_on_the_first_row_goes_to_the_start_and_down_on_the_last_to_the_end():
    assert C.move_vertical("ab\ncd", 1, 10, -1, None)[0] == 0
    assert C.move_vertical("ab\ncd", 4, 10, 1, None)[0] == 5


def test_other_keys_forget_the_goal_column():
    buf, pos, goal = C.apply_key("ab\ncd", 4, "up", "", 10, None)
    assert (pos, goal) == (1, 1)
    assert C.apply_key(buf, pos, "char", "x", 10, goal)[2] is None


def test_a_pasted_tab_becomes_spaces_and_control_bytes_are_dropped():
    r, w = os.pipe()
    os.write(w, b"a\tb\x07c\ndone\x1b[201~")
    text = C.read_paste(r)
    os.close(w); os.close(r)
    assert text == "a    bc\ndone"


# --- the picker's control flow ----------------------------------------------

def run_select(count, script):
    """Drive select_loop with a scripted key list; return (result, draws)."""
    pending = list(script)
    draws = []
    return (C.select_loop(count, lambda: pending.pop(0),
                          lambda i, first: draws.append(i)),
            draws)


def test_enter_commits_the_highlighted_row():
    assert run_select(3, ["down", "enter"])[0] == 1


def test_moving_the_highlight_commits_nothing_until_enter():
    # Ten moves, one Enter: the answer is where the cursor landed, and every
    # intermediate position was only ever drawn, never returned.
    result, draws = run_select(3, ["down", "down", "up", "down", "up",
                                   "up", "down", "down", "up", "enter"])
    assert result == 1
    assert len(draws) == 10          # every move redrew
    assert draws[0] == 0             # started at the top


def test_the_highlight_wraps_at_both_ends():
    assert run_select(3, ["up", "enter"])[0] == 2
    assert run_select(3, ["down", "down", "down", "enter"])[0] == 0


def test_ending_input_abandons_the_question():
    assert run_select(3, ["down", "eof"])[0] is None
    assert run_select(3, ["esc"])[0] is None


def test_ctrl_c_propagates():
    try:
        run_select(3, ["interrupt"])
    except KeyboardInterrupt:
        return
    raise AssertionError("expected KeyboardInterrupt")


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


# --- paste -------------------------------------------------------------------

def bracketed(text: bytes) -> bytes:
    return b"\x1b[200~" + text + b"\x1b[201~"


def test_a_pasted_paragraph_arrives_whole_instead_of_submitting():
    """Regression: a pasted newline reaches the editor as CR, which is also
    Enter. Without bracketed paste the buffer is submitted at the first line
    break and the rest is read as keystrokes -- input silently cut off."""
    r, w = os.pipe()
    os.write(w, bracketed(b"line one\r\nline two\rline three"))
    key, text = C.read_edit_key(r)
    os.close(w); os.close(r)
    assert key == "paste"
    assert text == "line one\nline two\nline three"   # CRLF and CR normalised


def test_pasted_text_is_inserted_at_the_cursor():
    buf, pos = typed(chars("ab") + [("left", ""), ("paste", "XY")])
    assert buf == "aXYb" and pos == 3


def test_a_paste_is_one_edit_not_one_per_character():
    # the whole paste lands in a single key event, so the redraw happens once
    buf, pos = typed([("paste", "a fairly long pasted requirement")])
    assert buf == "a fairly long pasted requirement"
    assert pos == len(buf)


def test_arrow_keys_still_decode_now_that_csi_is_parsed_in_full():
    r, w = os.pipe()
    os.write(w, b"\x1b[A\x1b[B\x1b[C\x1b[D\x1b[H\x1b[F\x1b[3~")
    got = [C.read_edit_key(r)[0] for _ in range(7)]
    os.close(w); os.close(r)
    assert got == ["up", "down", "right", "left", "home", "end", "delete"]


def test_delete_removes_under_the_cursor():
    assert typed(chars("ab") + [("home", ""), ("delete", "")]) == ("b", 0)


# --- the redraw must not climb into earlier output --------------------------

class FakeTTY(io.StringIO):
    def isatty(self): return True
    def fileno(self): return 0


def draw_once(buf, pos, cursor_row, width=20):
    """Run one _draw_input and return (emitted escape text, returned row)."""
    out = FakeTTY()
    c = C.Console(out=out, inp=FakeTTY(), color=False)
    c._width = lambda: width
    row = c._draw_input("> ", "> ", buf, pos, cursor_row)
    return out.getvalue(), row


def test_continuation_rows_are_indented_under_the_prompt():
    text, _ = draw_once("first\nsecond", 0, cursor_row=0)
    assert "\r\n  second" in text
    assert "> \x1b[7mf\x1b[27mirst" in text          # the caret is drawn in place


def test_the_caret_past_the_end_is_a_reverse_video_space():
    text, _ = draw_once("ab", 2, cursor_row=0)
    assert "> ab\x1b[7m \x1b[27m" in text


def test_the_terminal_cursor_is_parked_on_the_caret_cell():
    text, row = draw_once("ab\ncdef", 5, cursor_row=0)   # "cd|ef"
    assert row == 1
    assert text.endswith("f\r\x1b[4C")                     # indent 2 + column 2
    text, row = draw_once("ab\ncdef", 1, cursor_row=0)   # "a|b", a row above the end
    assert row == 0
    assert text.endswith("\x1b[1A\r\x1b[3C")


def leading_up(text):
    """How far the repaint moves up *before* it starts drawing. Only this one
    can escape the block; the later move is repositioning within it."""
    m = re.match(r"\x1b\[(\d+)A", text)
    return int(m.group(1)) if m else 0


def test_the_redraw_returns_to_the_top_of_its_own_block_only():
    """Regression: the caret is left on its own row, not the last row, so a
    repaint that moved up by (rows - 1) overshot whenever the caret sat above
    the end -- straight into the agent output above, which ESC[J then wiped."""
    # three rows of input, caret parked on the first
    text, row = draw_once("aaaa\nbbbb\ncccc", 2, cursor_row=0)
    assert row == 0                      # caret is on row 0 ...
    # ... so the next repaint must not climb at all
    text2, _ = draw_once("aaaa\nbbbb\ncccc", 2, cursor_row=row)
    assert leading_up(text2) == 0, "repaint climbed above its own block"


def test_the_repaint_climbs_exactly_as_far_as_the_caret_sat():
    for caret_row in (0, 1, 2):
        text, _ = draw_once("aaaa\nbbbb\ncccc", 0, cursor_row=caret_row)
        assert leading_up(text) == caret_row


def test_a_caret_on_the_last_row_reports_that_row():
    _, row = draw_once("aaaa\nbbbb", len("aaaa\nbbbb"), cursor_row=0)
    assert row == 1


def test_the_block_is_cleared_before_repainting():
    text, _ = draw_once("ab", 2, cursor_row=0)
    assert "\x1b[J" in text


def replay_rows(stream: str) -> list[int]:
    """Row offsets, relative to where the editor began, at each ESC[J clear.

    A negative offset means the clear happened above the editor's own first
    row -- i.e. it is erasing output that was printed before the prompt.
    """
    row, at_clears = 0, []
    i = 0
    while i < len(stream):
        if stream.startswith("\r\n", i):
            row += 1
            i += 2
            continue
        m = re.match(r"\x1b\[(\d*)([ABJ])", stream[i:])
        if m:
            n = int(m.group(1) or 1)
            if m.group(2) == "A":
                row -= n
            elif m.group(2) == "B":
                row += n
            else:
                at_clears.append(row)
            i += m.end()
            continue
        i += 1
    return at_clears


def test_an_edit_session_never_clears_above_its_own_first_row():
    """The end-to-end form of the regression: thread the caret row through a
    realistic session (multi-line entry, then editing back up into it) exactly
    as `_edit` does, and check every repaint clears at or below its own top."""
    script = (chars("first line") + [("newline", "")]
              + chars("second line") + [("newline", "")]
              + chars("third line")
              + [("home", "")] + [("up", "")]
              + chars("!") + [("left", "")] * 4 + chars("?")
              + [("backspace", "")])
    out = FakeTTY()
    c = C.Console(out=out, inp=FakeTTY(), color=False)
    c._width = lambda: 20
    buf, pos, at, goal = "", 0, 0, None
    for key, ch in script:
        at = c._draw_input("> ", "> ", buf, pos, at)
        buf, pos, goal = C.apply_key(buf, pos, key, ch, c._text_width("> "), goal)
    c._draw_input("> ", "> ", buf, pos, at)

    offsets = replay_rows(out.getvalue())
    assert offsets, "expected repaints"
    assert min(offsets) >= 0, f"cleared {abs(min(offsets))} row(s) above the block"


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


def test_choose_passes_through_a_typed_answer():
    c, _ = console("something else entirely\n")
    assert c.choose(QUESTION) == "something else entirely"


def test_ending_input_at_a_menu_is_also_a_finish():
    c, _ = console("")
    assert c.choose(QUESTION) is None
