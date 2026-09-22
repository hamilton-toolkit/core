"""Terminal rendering: styling, the question picker, the input editor, and the
working indicator.

Every session mode and `hamilton build` talk to the engineer through this one
class, so the interaction is identical whatever is running.

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

**Subagents show while they run.** Under the working indicator, one row per
running subagent: what it is, for how long, and what it is doing now. When one
finishes, a single line stays behind in the scrollback.

**The step shows too.** Each step of the work leaves a `▸` line and relabels
the indicator, so what is running now -- writing tests, reviewing them, coding
-- is readable at a glance and afterwards in the scrollback. `hamilton build`
draws its loop this way.

**Long results fold.** A list of headings unfolds one item at a time; what
was unfolded when the engineer leaves stays in the scrollback. Without a TTY
everything prints unfolded.

Imports no vendor SDK: this is Hamilton's own front end, and it renders
`protocol` types.
"""

from __future__ import annotations

import contextlib
import enum
import itertools
import os
import re
import shutil
import sys
import threading
import time

from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_input
from prompt_toolkit.output import create_output

from hamilton_core.session import protocol as P
from hamilton_core.session import widgets

ESC = "\x1b"
PROMPT = "> "
OTHER = "Type my own answer"
FINISH_ROW = "Finish this session"
EDIT_HINT = "Alt+Enter (or Ctrl+J) for a new line · Enter to send"
ENDED = "(the engineer ended the session)"
WORKING = "Engineering"
FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
MAX_ROWS = 5


class Outcome(enum.Enum):
    """What a question resolved to. `FINISH` is the engineer choosing to stop;
    at an iteration boundary that is a clean finish, mid-question it is an
    interruption, and `ask` and `choose` read it accordingly."""
    CHOICE = enum.auto()
    TEXT = enum.auto()
    FINISH = enum.auto()
    ABORT = enum.auto()


# Choices that duplicate the fixed rows Hamilton adds to every question: a
# catch-all ("Other") or a way out ("Exit session"). The agent is told not to
# offer them; any that slip through are dropped.
_FIXED_ROW_DUPLICATE = re.compile(
    r"^\W*(other|something else|type my own|none of these|exit|quit"
    r"|(finish|end|close)\s+(the\s+|this\s+)?session)\b", re.I)


def without_fixed_rows(q: P.Question) -> P.Question:
    choices = tuple(c for c in q.choices
                    if not _FIXED_ROW_DUPLICATE.match(c.label))
    return P.Question(q.prompt, choices, q.header)


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
    def yellow(self, s: str) -> str: return self._w("33", s)
    def red(self, s: str) -> str: return self._w("31", s)
    def heading(self, s: str) -> str: return self._w("1;36", s)


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


def activity_lines(rows, now: float, frame: str) -> list[str]:
    """What is running, as the indicator shows it, at most `MAX_ROWS`: what
    it is, for how long, and what it is doing now."""
    lines = []
    for row in rows[:MAX_ROWS]:
        line = f"  {frame} {row.label}  {elapsed(now - row.started)}"
        if row.doing:
            line += f" · {row.doing}"
        lines.append(line)
    if len(rows) > MAX_ROWS:
        lines.append(f"  … and {len(rows) - MAX_ROWS} more")
    return lines


class Rows:
    """The rows under the working indicator: whatever is running now.

    `snapshot` is read by the indicator's thread, so it is replaced whole
    after every change and never mutated in place. Pass the instance to
    `Console.follow`.
    """

    def __init__(self) -> None:
        self._rows: dict = {}
        self.snapshot: tuple = ()

    def __call__(self) -> tuple:
        return self.snapshot

    def start(self, key, label: str) -> None:
        self._rows[key] = P.Activity(str(key), label, time.monotonic())
        self._take()

    def doing(self, key, action: str) -> None:
        """What the row's work is doing now, in words."""
        row = self._rows.get(key)
        if row is not None:
            self._rows[key] = P.Activity(row.id, row.label, row.started, action)
            self._take()

    def stop(self, key) -> float:
        """Close the row; returns how long it ran, for the line left behind."""
        row = self._rows.pop(key, None)
        self._take()
        return time.monotonic() - row.started if row else 0.0

    def _take(self) -> None:
        self.snapshot = tuple(self._rows.values())


def echo(text: str) -> str:
    """How a sent answer is reprinted: the prompt, then the text with lines
    after a newline indented under it. Long lines are left for the terminal
    to wrap, so a copy from the scrollback has no breaks we added."""
    return PROMPT + text.replace("\n", "\n" + " " * len(PROMPT))


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
        self._shown = 0            # lines of the frame currently on screen
        self._label = WORKING      # what the indicator says it is doing
        self._activity = lambda: ()

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

    @property
    def interactive(self) -> bool:
        """Whether there is somebody at a terminal to ask. A run that cannot
        ask reports instead."""
        return self._interactive()

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

    def follow(self, activity) -> None:
        """Draw the rows `activity()` returns under the working indicator."""
        self._activity = activity

    def start_working(self, label: str = WORKING) -> None:
        """Animate until `stop_working`. Silent when not on a terminal."""
        self._label = label
        if not self._interactive() or self._thread is not None:
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._animate, args=(self._stop,), daemon=True)
        self._thread.start()

    def working_on(self, label: str) -> None:
        """Say what is being worked on now. The indicator picks it up on its
        next frame, so a step change does not restart the animation."""
        self._label = label

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
    def paused(self):
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
                self.start_working(self._label)

    def _animate(self, stop: threading.Event) -> None:
        started = time.monotonic()
        for frame in itertools.cycle(FRAMES):
            if stop.wait(0.1):
                return
            with self._lock:
                if stop.is_set():
                    return
                now = time.monotonic()
                tail = f" ({elapsed(now - started)})" if now - started >= 2 else ""
                lines = ([f"{frame} {self._label}…{tail}"]
                         + activity_lines(self._activity(), now, frame))
                # A wrapped line would throw off the count `_erase` goes up by.
                width = max(shutil.get_terminal_size().columns - 1, 10)
                self._out.write(self._erase() + "\n".join(
                    self.paint.dim(line[:width]) for line in lines))
                self._out.flush()
                self._shown = len(lines)

    def _erase(self) -> str:
        """What takes the frame on screen off it again: back to its first
        line, then clear to the end of the screen."""
        if not self._shown:
            return ""
        up = f"{ESC}[{self._shown - 1}A" if self._shown > 1 else ""
        return f"\r{up}{ESC}[J"

    def _clear_frame(self) -> None:
        if self._shown:
            self._out.write(self._erase())
            self._out.flush()
            self._shown = 0

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

    def step(self, label: str, detail: str = "") -> None:
        """A step of the agent's workflow starting. It stays in the scrollback,
        so the session reads back as the sequence of steps it ran."""
        self.say()
        self.say(f"{self.paint.cyan('▸')} {self.paint.bold(label)}"
                 + (f" {self.paint.dim('— ' + detail)}" if detail else ""))

    def subagent_done(self, label: str, ok: bool, seconds: float) -> None:
        if ok:
            self.say(self.paint.dim(f"  ✓ {label} ({elapsed(seconds)})"))
        else:
            self.say(self.paint.red(f"  ✗ {label} failed ({elapsed(seconds)})"))

    def browse(self, items: list[tuple[str, list[str]]]) -> None:
        """(heading, lines) items, folded, for the engineer to unfold. Then the
        list stays behind as it was left."""
        opened = set(range(len(items)))
        if self._interactive():
            with self.paused(), self._session():
                opened = widgets.folds(items).run()
        for i, (head, body) in enumerate(items):
            self.say(f"  {head}")
            if i in opened:
                for ln in body:
                    self.say(ln)

    def denial(self, path: str, reason: str) -> None:
        self.say(self.paint.red(f"  ✗ write to {path} denied: {reason}"))

    def error(self, message: str) -> None:
        self.say(self.paint.red(f"hamilton: {message}"))

    def next_message(self) -> str | None:
        """The engineer's own next message, or None to end the session."""
        with self.paused():
            self.say()
            raw = self._input(hint=f"Enter on its own ends the session · {EDIT_HINT}")
        if raw is None or raw.strip() == "" or raw.strip() in ("/exit", "/quit"):
            return None
        return raw

    def confirm(self, question: str) -> bool:
        """A yes/no, where yes is the default. EOF reads as no. The indicator
        is paused while it waits, or it would draw over the question."""
        with self.paused():
            answer = self._read(self.paint.bold(f"{question} [Y/n] "))
        return answer is not None and answer.lower() in ("", "y", "yes")

    def offer_resume(self, cp: P.Checkpoint) -> bool:
        self.say()
        self.say(self.paint.bold(
            f"An unfinished {cp.phase} session is on record "
            f"({cp.turns_completed} turn(s) in)."))
        if cp.last_summary:
            self.note(f"  last: {cp.last_summary}")
        return self.confirm("Resume it?")

    # --- questions -----------------------------------------------------------

    def ask(self, q: P.Question) -> str:
        """A question from the agent. Its answer goes back to the model, so
        finishing here reads as an interruption -- the engineer is leaving
        mid-thought, and the checkpoint should stay resumable."""
        q = without_fixed_rows(q)
        with self._question(q):
            if q.choices:
                outcome, value = self._resolve(q)
            else:
                value = self._free_text()
                outcome = Outcome.ABORT if value is None else Outcome.TEXT
        if outcome in (Outcome.ABORT, Outcome.FINISH):
            self.aborted = True
            return ENDED
        return value

    def choose(self, q: P.Question, finish: str = FINISH_ROW) -> str | None:
        """Hamilton's own menu: its rows and a way out, nothing typed. `None`
        means the engineer took the way out -- `finish` is what that row says,
        because ending a session and ending a build run are not the same
        thing."""
        with self._question(q):
            outcome, value = self._resolve(q, own_answer=False, finish=finish)
        return None if outcome in (Outcome.ABORT, Outcome.FINISH) else value

    def choose_many(self, prompt: str, options: list[tuple[str, str]]) -> list[str] | None:
        """Several of `options` ((value, label) rows), for Hamilton's own flow.
        The chosen values, or None if the engineer backs out."""
        with self._question(P.Question(prompt)):
            if not self._interactive():
                return self._numbered_many_choices(options)
            with self._session():
                chosen = widgets.checklist(options).run()
            if chosen:
                labels = dict(options)
                self._echo_chosen([labels[v] for v in chosen])
            return chosen

    def ask_text(self, prompt: str) -> str | None:
        """An open question from Hamilton's own flow. None if the engineer
        backs out -- unlike `ask`, that does not end the session."""
        with self._question(P.Question(prompt)):
            answer = self._input(hint=f"Enter on its own goes back · {EDIT_HINT}")
        return answer if answer and answer.strip() else None

    @contextlib.contextmanager
    def _question(self, q: P.Question):
        """Pause the indicator and show the question while it is answered."""
        with self.paused():
            self.say()
            if q.header:
                self.say(self.paint.heading(f"── {q.header} ──"))
            if q.prompt:
                self.say(markdown(q.prompt, self.paint))
            yield

    def _echo_chosen(self, labels: list[str]) -> None:
        self.say(self.paint.green("\n".join(f"  {label}" for label in labels)))

    def _resolve(self, q: P.Question, own_answer: bool = True,
                 finish: str = FINISH_ROW) -> tuple[Outcome, str]:
        if self._interactive():
            return self._pick(q, own_answer, finish)
        return self._numbered(q, own_answer, finish)

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
                text = widgets.editor(hint).prompt([("class:prompt", PROMPT)])
        except EOFError:
            return None
        self.say(echo(text))
        return text

    def _pick(self, q: P.Question, own_answer: bool,
              finish: str = FINISH_ROW) -> tuple[Outcome, str]:
        """The cursor list. Moving the highlight sends nothing; Enter does."""
        fixed = ([OTHER] if own_answer else []) + [finish]
        rows = ([(c.label, c.description) for c in q.choices]
                + [(label, "") for label in fixed])
        with self._session():
            idx = widgets.picker(rows).run()
        if idx is None:
            return Outcome.ABORT, ""
        label = rows[idx][0]
        if idx < len(q.choices):
            self._echo_chosen([label])
            return Outcome.CHOICE, label
        if label == finish:
            return Outcome.FINISH, ""
        answer = self._free_text()
        return (Outcome.TEXT, answer) if answer is not None else (Outcome.ABORT, "")

    def _numbered(self, q: P.Question, own_answer: bool,
                  finish: str = FINISH_ROW) -> tuple[Outcome, str]:
        """No-TTY fallback: the same question as a numbered list. Typing is
        already how you answer here, so only the way out is added."""
        for i, c in enumerate(q.choices, 1):
            tail = f" — {c.description}" if c.description else ""
            self.say(f"  {i}) {c.label}{tail}")
        last = len(q.choices) + 1
        self.say(f"  {last}) {finish}")
        while True:
            raw = self._read(f"choose 1-{last}"
                             f"{', or type your own answer' if own_answer else ''}> ")
            if raw is None:
                return Outcome.ABORT, ""
            if raw.isdigit():
                n = int(raw)
                if n == last:
                    return Outcome.FINISH, ""
                if 1 <= n <= len(q.choices):
                    return Outcome.CHOICE, q.choices[n - 1].label
                self.say("  not a choice on the list -- pick again")
            elif raw and own_answer:
                return Outcome.TEXT, raw

    def _numbered_many_choices(self, options: list[tuple[str, str]]) -> list[str] | None:
        """No-TTY fallback for `choose_many`: numbers separated by commas."""
        for i, (_value, label) in enumerate(options, 1):
            self.say(f"  {i}) {label}")
        while True:
            raw = self._read("choose numbers, e.g. 1,3 (empty to go back)> ")
            if not raw:
                return None
            picks = [p.strip() for p in raw.split(",")]
            if all(p.isdigit() and 1 <= int(p) <= len(options) for p in picks):
                return [options[int(p) - 1][0] for p in picks]
            self.say("  not numbers on the list -- pick again")
