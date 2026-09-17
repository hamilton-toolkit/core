"""Terminal rendering for a session: styling, the question picker, the input
editor, and the working indicator.

Three things matter here beyond looking tidy.

**Nothing is sent until Enter.** On a real terminal a question is a cursor
list: arrows or Tab move the highlight, and only Enter commits. Moving is free,
so a mis-pick costs a keystroke instead of the session -- which was the point
of driving the session in-process at all. There is no separate confirmation
step; Enter *is* the confirmation.

**Typing is not limited to one line.** Alt+Enter (or Ctrl+J, for terminals that
swallow the first) opens a new line; Enter sends. A requirement or a correction
is often a paragraph, and a single-line prompt quietly punishes that. Rows after
the `> ` prompt are indented under it, the caret is drawn as a reverse-video
cell, and all four arrow keys move it. Copy and paste stay with the terminal
(Ctrl+Shift+C/V); a paste arrives bracketed, so its newlines are text.

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
import select
import shutil
import sys
import threading
import time

from hamilton_core.session import protocol as P

try:  # POSIX only; absence just means the terminal features are unavailable
    import termios
    import tty
except ImportError:  # pragma: no cover -- exercised on Windows, not in CI
    termios = tty = None

ESC = "\x1b"
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


# --- layout -------------------------------------------------------------------

def text_rows(buf: str, width: int) -> list[tuple[int, int]]:
    """The buffer as screen rows: (start, end) slices of `buf`, hard-wrapped at
    `width` and split at explicit newlines. Hamilton wraps rather than leaving
    it to the terminal so the cursor arithmetic below is exact.

    A line that is empty, or that exactly fills its last row, gets an empty row
    after it: that is where the caret sits at its end."""
    rows: list[tuple[int, int]] = []
    start = 0
    for line in buf.split("\n"):
        n = len(line)
        rows.extend((start + i, start + min(i + width, n))
                    for i in range(0, n, width))
        if n % width == 0:
            rows.append((start + n, start + n))
        start += n + 1
    return rows


def caret_rc(buf: str, pos: int, width: int) -> tuple[int, int]:
    """(row, column) of the caret at `pos`, under `text_rows`."""
    rows = text_rows(buf, width)
    for r, (s, e) in enumerate(rows):
        # the end of a full row is shown at the start of the row after it
        if s <= pos < e or (pos == e and e - s < width):
            return r, pos - s
    s, _ = rows[-1]
    return len(rows) - 1, pos - s


def move_vertical(buf: str, pos: int, width: int, delta: int,
                  goal: int | None) -> tuple[int, int]:
    """Move the caret `delta` rows, keeping to the `goal` column across short
    rows. Past the first row it goes to the start, past the last to the end.
    Returns (pos, goal)."""
    rows = text_rows(buf, width)
    row, col = caret_rc(buf, pos, width)
    goal = col if goal is None else goal
    target = row + delta
    if target < 0:
        return 0, goal
    if target >= len(rows):
        return len(buf), goal
    s, e = rows[target]
    last = e - s if e - s < width else width - 1
    return s + min(goal, last), goal


# --- keys ---------------------------------------------------------------------

def read_key(read) -> str:
    """One keypress for the picker. `read(n)` returns up to n bytes."""
    ch = read(1)
    if not ch:
        return "eof"
    if ch == b"\x1b":
        return {b"[A": "up", b"[B": "down",
                b"[Z": "up"}.get(read(2), "esc")   # [Z is shift-tab
    if ch in (b"\r", b"\n"):
        return "enter"
    if ch == b"\t":
        return "down"
    if ch == b"\x03":
        return "interrupt"
    if ch == b"\x04":
        return "eof"
    return "other"


def _waiting(fd, timeout: float) -> bool:
    return bool(select.select([fd], [], [], timeout)[0])


# Bracketed paste: the terminal brackets pasted text in these markers, so a
# newline inside a paste is text rather than a Return. Without it a pasted
# paragraph submits at its first line break and the rest is read as keystrokes.
PASTE_ON = f"{ESC}[?2004h"
PASTE_OFF = f"{ESC}[?2004l"
PASTE_START = b"200~"
PASTE_END = b"\x1b[201~"

# The editor draws its own caret as a reverse-video cell, so the terminal's
# cursor is hidden while it runs.
HIDE_CURSOR = f"{ESC}[?25l"
SHOW_CURSOR = f"{ESC}[?25h"

_CSI = {b"A": "up", b"B": "down", b"C": "right", b"D": "left",
        b"H": "home", b"F": "end", b"1~": "home", b"7~": "home",
        b"4~": "end", b"8~": "end", b"3~": "delete"}


def _csi(fd) -> bytes:
    """The remainder of a CSI sequence, up to and including its final byte."""
    seq = b""
    while len(seq) < 32:
        ch = os.read(fd, 1)
        if not ch:
            break
        seq += ch
        if 0x40 <= ch[0] <= 0x7E:
            break
    return seq


def read_paste(fd) -> str:
    """Pasted text, to its end marker. Newlines are normalised: a terminal may
    send CR, LF or CRLF for the same line break."""
    buf = b""
    while not buf.endswith(PASTE_END):
        ch = os.read(fd, 1)
        if not ch:
            break
        buf += ch
    if buf.endswith(PASTE_END):
        buf = buf[:-len(PASTE_END)]
    text = buf.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
    # A tab or a stray control character would take a width the layout
    # arithmetic does not know about.
    text = text.replace("\t", "    ")
    return "".join(c for c in text if c == "\n" or c >= " ")


def read_edit_key(fd) -> tuple[str, str]:
    """One keypress for the input editor, as (name, text).

    Escape has to be disambiguated by waiting: bare Esc, Alt+Enter (`Esc` then
    Return), an arrow key (`Esc [ A`) and the start of a paste (`Esc [ 2 0 0 ~`)
    all begin the same way.
    """
    b = os.read(fd, 1)
    if not b:
        return "eof", ""
    if b == b"\x1b":
        if not _waiting(fd, 0.05):
            return "esc", ""
        nxt = os.read(fd, 1)
        if nxt in (b"\r", b"\n"):
            return "newline", ""
        if nxt == b"[":
            seq = _csi(fd)
            if seq == PASTE_START:
                return "paste", read_paste(fd)
            return _CSI.get(seq, "other"), ""
        return "other", ""
    if b == b"\r":
        return "submit", ""
    if b == b"\n":          # Ctrl+J, for terminals that keep Alt+Enter
        return "newline", ""
    if b == b"\x7f":
        return "backspace", ""
    if b == b"\x03":
        return "interrupt", ""
    if b == b"\x04":
        return "eof", ""
    if b == b"\x15":
        return "kill", ""
    if b < b" ":
        return "other", ""
    return "char", _utf8(fd, b)


def _utf8(fd, first: bytes) -> str:
    b0 = first[0]
    extra = 3 if b0 >= 0xF0 else 2 if b0 >= 0xE0 else 1 if b0 >= 0xC0 else 0
    data = first + (os.read(fd, extra) if extra else b"")
    return data.decode("utf-8", "replace")


def select_loop(count: int, read, draw) -> int | None:
    """Move a highlight over `count` rows until Enter. Returns the chosen index,
    or None if the engineer ended it. Pure control flow -- `read` and `draw` are
    injected, which is what makes it testable without a terminal."""
    idx, first = 0, True
    while True:
        draw(idx, first)
        first = False
        key = read()
        if key == "up":
            idx = (idx - 1) % count
        elif key == "down":
            idx = (idx + 1) % count
        elif key == "enter":
            return idx
        elif key == "interrupt":
            raise KeyboardInterrupt
        elif key in ("eof", "esc"):
            return None


def edit_keys(buf: str, pos: int, key: str, ch: str) -> tuple[str, int]:
    """Apply one keypress to the edit buffer. Pure, so the editing rules are
    testable without a terminal."""
    if key in ("char", "paste"):
        return buf[:pos] + ch + buf[pos:], pos + len(ch)
    if key == "newline":
        return buf[:pos] + "\n" + buf[pos:], pos + 1
    if key == "backspace" and pos:
        return buf[:pos - 1] + buf[pos:], pos - 1
    if key == "delete":
        return buf[:pos] + buf[pos + 1:], pos
    if key == "left":
        return buf, max(0, pos - 1)
    if key == "right":
        return buf, min(len(buf), pos + 1)
    if key == "home":
        return buf, 0
    if key == "end":
        return buf, len(buf)
    if key == "kill":
        return "", 0
    return buf, pos


def apply_key(buf: str, pos: int, key: str, ch: str, width: int,
              goal: int | None) -> tuple[str, int, int | None]:
    """`edit_keys` plus up/down, which need the layout. `goal` is the column
    up/down aim for; any other key forgets it."""
    if key in ("up", "down"):
        pos, goal = move_vertical(buf, pos, width, -1 if key == "up" else 1, goal)
        return buf, pos, goal
    buf, pos = edit_keys(buf, pos, key, ch)
    return buf, pos, None


class Console:
    """Everything the engineer sees. `aborted` goes true when they end the
    session at a prompt (EOF/Esc), which the driver checks after the turn."""

    def __init__(self, out=None, inp=None, color=None) -> None:
        self._out = out or sys.stderr
        self._in = inp or sys.stdin
        self.aborted = False
        self.paint = Paint(supports_color(self._out) if color is None else color)
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
        if termios is None:
            return False
        try:
            return bool(self._in.isatty() and self._out.isatty())
        except (AttributeError, ValueError):
            return False

    def _width(self) -> int:
        # One short of the real width: a line that exactly fills the terminal
        # leaves the cursor in a position terminals disagree about.
        return max(20, shutil.get_terminal_size((80, 24)).columns - 1)

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
            raw = self._input(self.paint.bold("> "),
                              hint="Enter on its own ends the session.")
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
            answer = self._input(self.paint.bold("> "))
            if answer is None:
                return None
            if answer.strip():
                return answer

    def _input(self, prompt: str, hint: str | None = None) -> str | None:
        """Multi-line where the terminal allows it, one line where it doesn't."""
        if not self._interactive():
            return self._read(prompt)
        self.note("  " + (f"{hint} " if hint else "") + EDIT_HINT)
        return self._edit(prompt)

    # --- the multi-line editor ----------------------------------------------

    def _edit(self, prompt: str) -> str | None:
        plain = re.sub(r"\x1b\[[0-9;]*m", "", prompt)
        fd = self._in.fileno()
        saved = termios.tcgetattr(fd)
        buf, pos, at, goal = "", 0, 0, None
        try:
            tty.setraw(fd)
            self._raw(PASTE_ON + HIDE_CURSOR)
            while True:
                at = self._draw_input(prompt, plain, buf, pos, at)
                key, ch = read_edit_key(fd)
                if key == "submit":
                    break
                if key == "interrupt":
                    raise KeyboardInterrupt
                if key == "eof" and not buf:
                    return None
                buf, pos, goal = apply_key(buf, pos, key, ch,
                                           self._text_width(plain), goal)
            # Repaint once more without the caret, ending on the last row, so
            # the newline below lands after the block rather than inside it.
            self._draw_input(prompt, plain, buf, len(buf), at, caret=False)
        finally:
            self._raw(PASTE_OFF + SHOW_CURSOR)
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        self._raw("\r\n")
        return buf

    def _text_width(self, plain: str) -> int:
        return max(1, self._width() - len(plain))

    def _draw_input(self, prompt: str, plain: str, buf: str, pos: int,
                    cursor_row: int, caret: bool = True) -> int:
        """Repaint the input block and leave the cursor where the caret is.

        The prompt sits on the first row; every row after it is indented by the
        prompt's width, so a multi-line answer reads as one left-aligned block.

        Returns the row the caret ended on, which the next call needs: the
        repaint starts by going back to the top of the block, and the cursor is
        wherever the caret was left -- not on the last row. Assuming otherwise
        walks the cursor up into output printed earlier, which the clear below
        then erases.
        """
        indent = len(plain)
        width = self._text_width(plain)
        rows = text_rows(buf, width)
        crow, ccol = caret_rc(buf, pos, width)

        out = [f"{ESC}[{cursor_row}A" if cursor_row else "", "\r", f"{ESC}[J"]
        for r, (s, e) in enumerate(rows):
            text = buf[s:e]
            if caret and r == crow:
                under = text[ccol] if ccol < len(text) else " "
                text = f"{text[:ccol]}{ESC}[7m{under}{ESC}[27m{text[ccol + 1:]}"
            # The prompt is styled; the arithmetic above used its plain text.
            out.append(("\r\n" + " " * indent if r else prompt) + text)

        end_row = len(rows) - 1
        if end_row > crow:
            out.append(f"{ESC}[{end_row - crow}A")
        out.append(f"\r{ESC}[{indent + ccol}C" if indent + ccol else "\r")
        self._raw("".join(out))
        return crow

    # --- the picker -----------------------------------------------------------

    def _pick(self, q: P.Question) -> tuple[str, str]:
        """The cursor list. Moving the highlight sends nothing; Enter does."""
        rows = ([(c.label, c.description) for c in q.choices]
                + [(OTHER, ""), (FINISH_ROW, "")])
        fd = self._in.fileno()
        saved = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            idx = select_loop(
                len(rows),
                lambda: read_key(lambda n: os.read(fd, n)),
                lambda i, first: self._draw(rows, i, first),
            )
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)

        self._erase(len(rows) + 1)
        if idx is None:
            return ABORT, ""
        if idx == len(q.choices):               # "type my own answer"
            answer = self._free_text()
            return (TEXT, answer) if answer is not None else (ABORT, "")
        if idx == len(q.choices) + 1:           # "finish this session"
            return FINISH, ""
        self.say(self.paint.green(f"  {rows[idx][0]}"))
        return CHOICE, rows[idx][0]

    def _draw(self, rows, idx: int, first: bool) -> None:
        # Raw mode: newlines must carry the return themselves. Rows are clipped
        # to the terminal: a line that wrapped would throw the cursor-up count
        # out and corrupt every redraw after it.
        width = self._width()
        out = [] if first else [f"{ESC}[{len(rows) + 1}A"]
        for i, (label, desc) in enumerate(rows):
            body = f" {'❯' if i == idx else ' '} {label}"[:width]
            tail = f"  {desc}"[:max(0, width - len(body))] if desc else ""
            line = self.paint.selected(body) if i == idx else body
            if tail:
                line += self.paint.dim(tail)
            out.append(f"{ESC}[2K{line}\r\n")
        out.append(f"{ESC}[2K{self.paint.dim('  ' + PICK_HINT)}\r\n")
        self._raw("".join(out))

    def _erase(self, lines: int) -> None:
        """Clear the list we drew, leaving the cursor where it started."""
        self._raw(f"{ESC}[{lines}A" + f"{ESC}[2K\n" * lines + f"{ESC}[{lines}A")

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
