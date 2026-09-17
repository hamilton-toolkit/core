"""The `prompt_toolkit` widgets the console runs: the multi-line editor, the
single-choice picker and the multi-choice checklist.

Each erases itself when done; the console reprints what was chosen or typed as
plain text. Nothing here knows about questions or sessions.
"""

from __future__ import annotations

from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application, get_app
from prompt_toolkit.cursor_shapes import CursorShape
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings, KeyPress
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Dimension, HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import CheckboxList, RadioList

PICK_HINT = "↑/↓ or Tab to move · Enter to select"
CHECK_HINT = "↑/↓ to move · Space to tick · Enter to confirm · Esc to go back"

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
