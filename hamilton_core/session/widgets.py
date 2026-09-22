"""The `prompt_toolkit` widgets the console runs: the multi-line editor, the
single-choice picker, the multi-choice checklist, the fold-out list and the
tree browser.

Each erases itself when done; the console reprints what was chosen or typed as
plain text. Nothing here knows about questions or sessions.
"""

from __future__ import annotations

from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application, get_app
from prompt_toolkit.cursor_shapes import CursorShape
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI, FormattedText, to_formatted_text
from prompt_toolkit.key_binding import KeyBindings, KeyPress
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Dimension, HSplit, Layout, ScrollOffsets, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import CheckboxList, RadioList

PICK_HINT = "↑/↓ or Tab to move · Enter to select"
CHECK_HINT = "↑/↓ to move · Space to tick · Enter to confirm · Esc to go back"
FOLD_HINT = "↑/↓ to move · Enter to unfold or fold · Esc to leave"
TREE_HINT = "↑/↓ to move · ←/→ to fold or unfold · Enter for details · Esc to leave"
DETAIL_HINT = "↑/↓ to scroll · Esc or Enter to go back"

STYLE = Style.from_dict({
    "prompt": "bold",
    "hint": "ansibrightblack",
    "description": "ansibrightblack",
    "chosen": "bold ansicyan",
})


def editor(hint: str) -> PromptSession:
    """Multi-line input: Enter sends, Alt+Enter or Ctrl+J adds a line. The
    hint shows while the input is empty."""
    kb = KeyBindings()

    @kb.add("enter")
    def _send(event):
        event.current_buffer.validate_and_handle()

    @kb.add("escape", "enter")
    @kb.add("c-j")
    def _newline(event):
        event.current_buffer.insert_text("\n")

    return PromptSession(
        multiline=True,
        key_bindings=kb,
        prompt_continuation=lambda width, _line, _wrap: " " * width,
        cursor=CursorShape.BLOCK,
        placeholder=FormattedText([("class:hint", hint)]),
        erase_when_done=True,
        style=STYLE,
    )


def picker(rows: list[tuple[str, str]]) -> Application:
    """One row of (label, description). The app's result is the chosen row's
    index, or None if the engineer left."""
    options = [(i, FormattedText([("", label)]
                                 + ([("class:description", f"  {desc}")] if desc else [])))
               for i, (label, desc) in enumerate(rows)]
    radio = RadioList(
        values=options,
        select_on_focus=True,
        open_character="",
        select_character="❯",
        close_character="",
        show_cursor=False,
        show_numbers=False,
        selected_style="",
        checked_style="class:chosen",
        show_scrollbar=False,
    )
    return _list(radio, PICK_HINT, lambda: radio.current_value)


def checklist(options: list[tuple[str, str]]) -> Application:
    """Several of (value, label). The app's result is the ticked values, or
    the highlighted one if none is ticked, or None if the engineer left."""
    boxes = CheckboxList(
        values=options,
        open_character="[",
        select_character="x",
        close_character="]",
        selected_style="class:chosen",
        checked_style="",
    )

    def ticked():
        # `_selected_index` is the highlighted row; the widget has no public
        # accessor for it.
        return (list(boxes.current_values)
                or [boxes.values[boxes._selected_index][0]])

    return _list(boxes, CHECK_HINT, ticked)


def folds(items: list[tuple[str, list[str]]]) -> Application:
    """Headings the engineer unfolds one at a time: (heading, lines) each,
    both may carry ANSI colour. The app's result is the indices open when
    they left."""
    at, opened = [0], set()

    def text():
        out = []
        for i, (head, body) in enumerate(items):
            out.append(("class:chosen", "❯ ") if i == at[0] else ("", "  "))
            out += to_formatted_text(ANSI(head)) + [("", "\n")]
            if i in opened:
                for ln in body:
                    out += to_formatted_text(ANSI(ln)) + [("", "\n")]
        return out[:-1]

    def row():
        return sum(1 + (len(body) if i in opened else 0)
                   for i, (_h, body) in enumerate(items[:at[0]]))

    kb = KeyBindings()

    @kb.add("down")
    @kb.add("tab")
    def _down(event):
        at[0] = min(at[0] + 1, len(items) - 1)

    @kb.add("up")
    @kb.add("s-tab")
    def _up(event):
        at[0] = max(at[0] - 1, 0)

    @kb.add("enter")
    @kb.add("space")
    def _toggle(event):
        opened.symmetric_difference_update({at[0]})

    @kb.add("right")
    def _unfold(event):
        opened.add(at[0])

    @kb.add("left")
    def _fold(event):
        opened.discard(at[0])

    @kb.add("escape", eager=True)
    @kb.add("q")
    @kb.add("c-d")
    @kb.add("c-c")
    def _leave(event):
        event.app.exit(result=set(opened))

    # The open item's lines are kept in view below its heading, as far as
    # half the window allows.
    window = Window(
        FormattedTextControl(text, get_cursor_position=lambda: Point(0, row()),
                             focusable=True, show_cursor=False),
        wrap_lines=False,
        scroll_offsets=ScrollOffsets(
            bottom=lambda: len(items[at[0]][1]) if at[0] in opened else 0),
        height=lambda: Dimension(max=max(3, get_app().output.get_size().rows - 2)))
    hint_row = Window(FormattedTextControl([("class:hint", f"  {FOLD_HINT}")]),
                      height=1)
    return Application(
        layout=Layout(HSplit([window, hint_row]), focused_element=window),
        key_bindings=kb,
        style=STYLE,
        erase_when_done=True,
    )


def tree(items: list[tuple[int, str, list[str]]]) -> Application:
    """A tree the engineer walks: (depth, heading, details) each, in pre-order,
    so an item's children are the deeper items right after it. Headings and
    details may carry ANSI colour. Enter shows the selected item's details.
    The app's result is the index selected when they left."""
    at, folded, reading, top = [0], set(), [False], [0]

    def depth(i):
        return items[i][0]

    def has_children(i):
        return i + 1 < len(items) and depth(i + 1) > depth(i)

    def parent(i):
        return next((j for j in range(i - 1, -1, -1) if depth(j) < depth(i)), None)

    def visible():
        out, hide_below = [], None
        for i in range(len(items)):
            if hide_below is not None and depth(i) > hide_below:
                continue
            hide_below = depth(i) if i in folded else None
            out.append(i)
        return out

    def page():
        return max(3, get_app().output.get_size().rows - 2)

    def text():
        out = []
        if reading[0]:
            for ln in items[at[0]][2][top[0]:]:
                out += to_formatted_text(ANSI(ln)) + [("", "\n")]
            return out[:-1]
        for i in visible():
            d, head, _ = items[i]
            out.append(("class:chosen", "❯ ") if i == at[0] else ("", "  "))
            mark = ("▸ " if i in folded else "▾ ") if has_children(i) else "  "
            out.append(("", "  " * d + mark))
            out += to_formatted_text(ANSI(head)) + [("", "\n")]
        return out[:-1]

    def row():
        return 0 if reading[0] else visible().index(at[0])

    def move(step):
        rows = visible()
        at[0] = rows[max(0, min(rows.index(at[0]) + step, len(rows) - 1))]

    def scroll(step):
        last = max(0, len(items[at[0]][2]) - page())
        top[0] = max(0, min(top[0] + step, last))

    kb = KeyBindings()
    browsing = Condition(lambda: not reading[0])
    detail = Condition(lambda: reading[0])

    @kb.add("down", filter=browsing)
    @kb.add("tab", filter=browsing)
    def _down(event):
        move(1)

    @kb.add("up", filter=browsing)
    @kb.add("s-tab", filter=browsing)
    def _up(event):
        move(-1)

    @kb.add("right", filter=browsing)
    def _unfold(event):
        if at[0] in folded:
            folded.discard(at[0])
        elif has_children(at[0]):
            at[0] += 1

    @kb.add("left", filter=browsing)
    def _fold(event):
        if has_children(at[0]) and at[0] not in folded:
            folded.add(at[0])
        elif parent(at[0]) is not None:
            at[0] = parent(at[0])

    @kb.add("enter", filter=browsing)
    def _read(event):
        reading[0], top[0] = True, 0

    @kb.add("escape", eager=True, filter=browsing)
    @kb.add("q", filter=browsing)
    @kb.add("c-d")
    @kb.add("c-c")
    def _leave(event):
        event.app.exit(result=at[0])

    @kb.add("down", filter=detail)
    def _scroll_down(event):
        scroll(1)

    @kb.add("up", filter=detail)
    def _scroll_up(event):
        scroll(-1)

    @kb.add("pagedown", filter=detail)
    def _page_down(event):
        scroll(page())

    @kb.add("pageup", filter=detail)
    def _page_up(event):
        scroll(-page())

    @kb.add("escape", eager=True, filter=detail)
    @kb.add("enter", filter=detail)
    @kb.add("left", filter=detail)
    @kb.add("q", filter=detail)
    def _back(event):
        reading[0] = False

    window = Window(
        FormattedTextControl(text, get_cursor_position=lambda: Point(0, row()),
                             focusable=True, show_cursor=False),
        wrap_lines=detail,
        height=lambda: Dimension(max=page()))
    hint_row = Window(FormattedTextControl(
        lambda: [("class:hint", f"  {DETAIL_HINT if reading[0] else TREE_HINT}")]),
        height=1)
    return Application(
        layout=Layout(HSplit([window, hint_row]), focused_element=window),
        key_bindings=kb,
        style=STYLE,
        erase_when_done=True,
    )


def _list(widget, hint: str, accept) -> Application:
    """A list the engineer moves through and confirms with Enter. The result
    is `accept()`, or None if they leave (Esc, Ctrl+D)."""
    kb = KeyBindings()

    @kb.add("enter", eager=True)
    def _confirm(event):
        event.app.exit(result=accept())

    @kb.add("tab")
    def _down(event):
        event.app.key_processor.feed(KeyPress(Keys.Down), first=True)

    @kb.add("s-tab")
    def _up(event):
        event.app.key_processor.feed(KeyPress(Keys.Up), first=True)

    @kb.add("escape", eager=True)
    @kb.add("c-d")
    def _leave(event):
        event.app.exit(result=None)

    @kb.add("c-c")
    def _interrupt(event):
        event.app.exit(exception=KeyboardInterrupt())

    # A long list scrolls instead of pushing its top off the screen.
    widget.window.height = lambda: Dimension(
        max=max(3, get_app().output.get_size().rows - 4))
    hint_row = Window(FormattedTextControl([("class:hint", f"  {hint}")]),
                      height=1)
    return Application(
        layout=Layout(HSplit([widget, hint_row]), focused_element=widget),
        key_bindings=kb,
        style=STYLE,
        erase_when_done=True,
    )
