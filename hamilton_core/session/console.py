"""Terminal rendering for a session: styling, the question picker, the input
editor, and the working indicator.

Every session mode talks to the engineer through this one class, so the
interaction is identical whatever the mode.

**Nothing is sent until Enter.** On a real terminal a question is a cursor
list: arrows or Tab move the highlight, and only Enter commits. Moving is free,
so a mis-pick costs a keystroke instead of the session -- which was the point
of driving the session in-process at all.

**Typing is not limited to one line.** Alt+Enter (or Ctrl+J) opens a new line;
Enter sends. The editor and the picker are `prompt_toolkit`, not our own: it
owns cursor movement, wrapping and bracketed paste. Both erase themselves when
done, and what was sent is reprinted as plain text. The terminal wraps that
itself, so copying it from the scrollback gives the text as typed, without
line breaks the editor's own wrapping would add.

**It degrades rather than breaks.** Without a TTY (a pipe, a test, a dumb
terminal) questions render as a numbered list, input is read a line at a time,
the working indicator stays silent and colour switches itself off. `NO_COLOR`
is honoured.

Imports no vendor SDK: this is Hamilton's own front end, and it renders
`protocol` types.
"""

from __future__ import annotations

import contextlib
import itertools
import os
import re
import sys
import threading
import time

from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application, create_app_session
from prompt_toolkit.cursor_shapes import CursorShape
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.input import create_input
from prompt_toolkit.key_binding import KeyBindings, KeyPress
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output import create_output
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import RadioList

from hamilton_core.session import protocol as P

ESC = "\x1b"
PROMPT = "> "
OTHER = "Type my own answer"
FINISH_ROW = "Finish this session"

# What a question resolved to. `FINISH` is the engineer choosing to stop; at an
# iteration boundary that is a clean finish, mid-question it is an interruption,
# and the two callers below read it accordingly.
CHOICE, TEXT, FINISH, ABORT = "choice", "text", "finish", "abort"
PICK_HINT = "↑/↓ or Tab to move · Enter to select"
EDIT_HINT = "Alt+Enter (or Ctrl+J) for a new line · Enter to send"
ENDED = "(the engineer ended the session)"
WORKING = "Engineering"
FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

STYLE = Style.from_dict({
    "prompt": "bold",
    "hint": "ansibrightblack",
    "description": "ansibrightblack",
    "chosen": "bold ansicyan",
})


def supports_color(stream) -> bool:
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


class Paint:
    """ANSI styling, or a pass-through when the stream cannot show it."""

    def __init__(self, on: bool) -> None:
        self.on = on

    def _w(self, code: str, s: str) -> str:
        return f"{ESC}[{code}m{s}{ESC}[0m" if self.on else s

    def bold(self, s: str) -> str: return self._w("1", s)
    def dim(self, s: str) -> str: return self._w("2", s)
    def cyan(self, s: str) -> str: return self._w("36", s)
    def green(self, s: str) -> str: return self._w("32", s)
    def red(self, s: str) -> str: return self._w("31", s)
    def selected(self, s: str) -> str: return self._w("1;36", s)


_MD_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
_MD_CODE = re.compile(r"`([^`\n]+)`")


def markdown(text: str, paint: Paint) -> str:
    """Just enough inline Markdown to make agent prose readable -- bold, code
    spans and ATX headings. Not a renderer; the agent's text is the content and
    is otherwise left alone."""
    text = _MD_BOLD.sub(lambda m: paint.bold(m.group(1)), text)
    text = _MD_CODE.sub(lambda m: paint.cyan(m.group(1)), text)
    lines = []
    for line in text.splitlines():
        if line.startswith("#"):
            lines.append(paint.bold(line.lstrip("#").lstrip()))
        else:
            lines.append(line)
    return "\n".join(lines)


def elapsed(seconds: float) -> str:
    s = int(seconds)
    return f"{s}s" if s < 60 else f"{s // 60}m{s % 60:02d}s"


def echo(text: str) -> str:
    """How a sent answer is reprinted: the prompt, then the text with lines
    after a newline indented under it. Long lines are left for the terminal
    to wrap, so a copy from the scrollback has no breaks we added."""
    return PROMPT + text.replace("\n", "\n" + " " * len(PROMPT))


# --- prompt_toolkit widgets ---------------------------------------------------

def editor(hint: str) -> PromptSession:
    """The multi-line input every prompt uses."""
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
    """The cursor list for a question. `rows` are (label, description); the
    app's result is the chosen row's index, or None if the engineer left."""
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
    kb = KeyBindings()

    @kb.add("enter", eager=True)
    def _select(event):
        event.app.exit(result=radio.current_value)

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

    hint = Window(FormattedTextControl([("class:hint", f"  {PICK_HINT}")]),
                  height=1)
    return Application(
        layout=Layout(HSplit([radio, hint]), focused_element=radio),
        key_bindings=kb,
        style=STYLE,
        erase_when_done=True,
    )


class Console:
    """Everything the engineer sees. `aborted` goes true when they end the
    session at a prompt (EOF/Esc), which the driver checks after the turn.

    `terminal` returns the context `prompt_toolkit` runs in. It defaults to
    this console's own streams; tests pass a pipe input instead, which also
    makes the console interactive."""

    def __init__(self, out=None, inp=None, color=None, terminal=None) -> None:
        self._out = out or sys.stderr
        self._in = inp or sys.stdin
        self.aborted = False
        self.paint = Paint(supports_color(self._out) if color is None else color)
        self._terminal = terminal
        self._lock = threading.RLock()
        self._stop = None          # set while the indicator thread runs
        self._thread = None
        self._shown = False        # a frame is currently on screen

    # --- plumbing ------------------------------------------------------------

    def say(self, line: str = "") -> None:
        with self._lock:
            self._clear_frame()
            print(line, file=self._out, flush=True)

    def _raw(self, text: str) -> None:
        with self._lock:
            self._clear_frame()
            self._out.write(text)
            self._out.flush()

    def _read(self, prompt: str) -> str | None:
        """A line from the engineer, or None on EOF."""
        self._raw(prompt)
        line = self._in.readline()
        if line == "":
            self.say()
            return None
        return line.strip()

    def _interactive(self) -> bool:
        if self._terminal is not None:
            return True
        try:
            return bool(self._in.isatty() and self._out.isatty())
        except (AttributeError, ValueError):
            return False

    def _session(self):
        if self._terminal is not None:
            return self._terminal()
        return create_app_session(input=create_input(self._in),
                                  output=create_output(self._out))

    # --- the working indicator -----------------------------------------------

    def start_working(self, label: str = WORKING) -> None:
        """Animate until `stop_working`. Silent when not on a terminal."""
        if not self._interactive() or self._thread is not None:
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._animate, args=(label, self._stop), daemon=True)
        self._thread.start()

    def stop_working(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=1.0)
        self._thread = None
        with self._lock:
            self._clear_frame()

    @property
    def working(self) -> bool:
        return self._thread is not None

    @contextlib.contextmanager
    def _paused(self):
        """Suspend the indicator around anything that reads or draws. The
        agent asks its questions from a worker thread, so without this the
        animation would scribble over the picker."""
        was = self.working
        if was:
            self.stop_working()
        try:
            yield
        finally:
            if was:
                self.start_working()

    def _animate(self, label: str, stop: threading.Event) -> None:
        started = time.monotonic()
        for frame in itertools.cycle(FRAMES):
            if stop.wait(0.1):
                return
            with self._lock:
                if stop.is_set():
                    return
                since = time.monotonic() - started
                tail = f" ({elapsed(since)})" if since >= 2 else ""
                self._out.write(
                    f"\r{ESC}[2K" + self.paint.dim(f"{frame} {label}…{tail}"))
                self._out.flush()
                self._shown = True

    def _clear_frame(self) -> None:
        if self._shown:
            self._out.write(f"\r{ESC}[2K")
            self._out.flush()
            self._shown = False

    # --- what the engineer sees ---------------------------------------------

    def banner(self, text: str) -> None:
        for line in text.splitlines():
            if set(line.strip()) == {"─"}:
                self.say(self.paint.dim(line))
            elif line.startswith("Hamilton"):
                self.say(self.paint.bold(self.paint.cyan(line)))
            else:
                self.say(line)

    def note(self, line: str) -> None:
        self.say(self.paint.dim(line))

    def agent_text(self, text: str) -> None:
        self.say()
        self.say(markdown(text, self.paint))

    def denial(self, path: str, reason: str) -> None:
        self.say(self.paint.red(f"  ✗ write to {path} denied: {reason}"))

    def error(self, message: str) -> None:
        self.say(self.paint.red(f"hamilton: {message}"))

    def prompt_turn(self) -> str | None:
        """The engineer's own next message, or None to end the session."""
        with self._paused():
            self.say()
            raw = self._input(hint=f"Enter on its own ends the session · {EDIT_HINT}")
        if raw is None or raw.strip() == "" or raw.strip() in ("/exit", "/quit"):
            return None
        return raw

    def offer_resume(self, cp: P.Checkpoint) -> bool:
        self.say()
        self.say(self.paint.bold(
            f"An unfinished {cp.phase} session is on record "
            f"({cp.turns_completed} turn(s) in)."))
        if cp.last_summary:
            self.note(f"  last: {cp.last_summary}")
        answer = self._read(self.paint.bold("Resume it? [Y/n] "))
        return answer is not None and answer.lower() in ("", "y", "yes")

    # --- questions -----------------------------------------------------------

    def ask(self, q: P.Question) -> str:
        """A question from the agent. Its answer goes back to the model, so
        finishing here reads as an interruption -- the engineer is leaving
        mid-thought, and the checkpoint should stay resumable."""
        with self._paused():
            self._show(q)
            if not q.choices:
                answer = self._free_text()
                if answer is None:
                    self.aborted = True
                    return ENDED
                return answer
            kind, value = self._resolve(q)
            if kind in (ABORT, FINISH):
                self.aborted = True
                return ENDED
            return value

    def choose(self, q: P.Question) -> str | None:
        """Hamilton's own menu. `None` means the engineer is done."""
        with self._paused():
            self._show(q)
            kind, value = self._resolve(q)
            return None if kind in (ABORT, FINISH) else value

    def _show(self, q: P.Question) -> None:
        self.say()
        if q.header:
            self.say(self.paint.selected(f"── {q.header} ──"))
        if q.prompt:
            self.say(markdown(q.prompt, self.paint))

    def _resolve(self, q: P.Question) -> tuple[str, str]:
        return self._pick(q) if self._interactive() else self._numbered(q)

    def _free_text(self) -> str | None:
        while True:
            answer = self._input(hint=EDIT_HINT)
            if answer is None:
                return None
            if answer.strip():
                return answer

    def _input(self, hint: str) -> str | None:
        """Multi-line where the terminal allows it, one line where it doesn't.
        On a terminal the editor erases itself and the answer is reprinted."""
        if not self._interactive():
            return self._read(PROMPT)
        try:
            with self._session():
                text = editor(hint).prompt([("class:prompt", PROMPT)])
        except EOFError:
            return None
        self.say(echo(text))
        return text

    def _pick(self, q: P.Question) -> tuple[str, str]:
        """The cursor list. Moving the highlight sends nothing; Enter does."""
        rows = ([(c.label, c.description) for c in q.choices]
                + [(OTHER, ""), (FINISH_ROW, "")])
        with self._session():
            idx = picker(rows).run()
        if idx is None:
            return ABORT, ""
        if idx == len(q.choices):               # "Type my own answer"
            answer = self._free_text()
            return (TEXT, answer) if answer is not None else (ABORT, "")
        if idx == len(q.choices) + 1:           # "Finish this session"
            return FINISH, ""
        self.say(self.paint.green(f"  {rows[idx][0]}"))
        return CHOICE, rows[idx][0]

    def _numbered(self, q: P.Question) -> tuple[str, str]:
        """No-TTY fallback: the same question as a numbered list. Typing is
        already how you answer here, so only the finish row is added."""
        for i, c in enumerate(q.choices, 1):
            tail = f" — {c.description}" if c.description else ""
            self.say(f"  {i}) {c.label}{tail}")
        last = len(q.choices) + 1
        self.say(f"  {last}) {FINISH_ROW}")
        while True:
            raw = self._read(f"choose 1-{last}, or type your own answer> ")
            if raw is None:
                return ABORT, ""
            if raw.isdigit():
                n = int(raw)
                if n == last:
                    return FINISH, ""
                if 1 <= n <= len(q.choices):
                    return CHOICE, q.choices[n - 1].label
                self.say("  not a choice on the list -- pick again, or type an answer")
            elif raw:
                return TEXT, raw
