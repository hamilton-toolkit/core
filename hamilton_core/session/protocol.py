"""Hamilton's own agent-session vocabulary. Imports no vendor SDK, by design.

Everything the session loop renders, persists or decides is expressed in these
types. An adapter (today `claude_sdk_adapter`, tomorrow possibly a hand-rolled
multi-model harness) translates a vendor's message stream into these events and
nothing else -- so swapping vendors touches the adapter, not the UX.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import AsyncIterator, Awaitable, Callable, Protocol

# The agent prints this literal line when its phase workflow is finished; the
# skill (templates/prompts/hamilton.md) instructs it to. Hamilton watches for
# it and ends the session itself -- otherwise the session sits open after the
# work is done and the engineer has to know to exit.
SENTINEL = "HAMILTON_SESSION_DONE"

SESSION_REL = os.path.join(".hamilton", "session")


# --- what the engineer is asked -----------------------------------------------

@dataclass(frozen=True)
class Choice:
    label: str
    description: str = ""


@dataclass(frozen=True)
class Question:
    """A question the agent wants answered. `choices` may be empty, in which
    case it is a free-text question."""
    prompt: str
    choices: tuple[Choice, ...] = ()
    header: str = ""


# An answerer renders a Question and returns the engineer's answer. It is
# called from the adapter, and it is where "let me re-pick before that is
# sent" lives: nothing reaches the model until this returns.
Answerer = Callable[[Question], str]

# Given a path a tool wants to write, return None to allow or the denial
# message. `hamilton_core.guard.decide` is the implementation.
WritePolicy = Callable[[str], "str | None"]

# What a running task is doing now, in words -- "editing src/x.php" -- for the
# row the engineer watches. The adapter words it; nothing else knows a
# vendor's tool names.
OnAction = Callable[[str], None]


# --- what comes back out of a turn --------------------------------------------

@dataclass(frozen=True)
class AgentText:
    text: str


@dataclass(frozen=True)
class ToolDenied:
    path: str
    reason: str


@dataclass(frozen=True)
class PhaseDone:
    """The agent emitted SENTINEL: this phase's workflow is finished. Raised
    by `agent.Agent` at the end of that turn, not by an adapter."""
    summary: str = ""


@dataclass(frozen=True)
class SessionError:
    message: str


@dataclass(frozen=True)
class SubagentDone:
    """A subagent the agent ran has finished."""
    label: str
    ok: bool
    elapsed: float


Event = AgentText | ToolDenied | PhaseDone | SessionError | SubagentDone


# --- what an adapter reports besides that -------------------------------------

@dataclass(frozen=True)
class TurnEnded:
    """A turn the agent ran is finished. `by_agent` marks one the agent
    started itself -- a task reporting back, say -- rather than the turn the
    engineer's message started. Which of them hands the engineer their prompt
    back is `agent.Agent`'s decision, not an adapter's."""
    by_agent: bool = False


@dataclass(frozen=True)
class TaskStarted:
    """A subagent of the agent's is running. `id` is the adapter's own handle.

    It is the *task* that started, not the tool call that asked for one: a
    vendor may hand the agent its task back the moment it is launched, and a
    row that closed there would report a subagent as done while it works.
    """
    id: str
    label: str


@dataclass(frozen=True)
class TaskProgress:
    """What a running subagent is doing now, in words."""
    id: str
    doing: str


@dataclass(frozen=True)
class TaskEnded:
    """A tool call of the agent's finished. Ids that were never started as a
    task are ignored, so an adapter need not remember which ids were tasks."""
    id: str
    ok: bool


StreamEvent = Event | TurnEnded | TaskStarted | TaskProgress | TaskEnded


@dataclass(frozen=True)
class Activity:
    """A running subagent, as the engineer sees it. `started` is a
    `time.monotonic()` reading."""
    id: str
    label: str
    started: float
    doing: str = ""                 # its latest action, in words


# --- the adapter seam ---------------------------------------------------------

class AgentAdapter(Protocol):
    """A live agent session: messages in, one stream of events out.

    The stream runs for the whole session, not per turn -- the agent can act
    between the engineer's messages, and must be heard when it does. Turns,
    subagent tracking and the completion sentinel are built on top of it in
    `agent.Agent`, once for every vendor.

    Construction carries the rest (project root, answerer, write policy, and
    the session reference to resume from), so this interface stays small
    enough for a future non-SDK harness to implement without inheriting any of
    today's vendor assumptions.
    """

    session_ref: str | None

    async def connect(self) -> None:
        ...

    async def send(self, text: str) -> None:
        ...

    def events(self) -> AsyncIterator[StreamEvent]:
        ...

    async def close(self) -> None:
        ...


class Judge(Protocol):
    """One prompt in, one answer out, from a fresh session with no tools, no
    project settings and nothing to resume. `hamilton review` asks it; what it
    can judge is exactly what the prompt holds."""

    async def ask(self, prompt: str) -> str:
        ...


class Worker(Protocol):
    """One piece of work, done by an agent with tools, in the project.

    `hamilton build` drives the loop itself and calls a worker for the things
    that need judgement -- planning a surface, writing a test, revising one,
    writing the implementation, drafting a clarified criterion. Each call is
    its own session: it starts from the prompt, does the work in the project
    (with the project's own conventions), and returns what it has to say
    about it. Nothing carries over, which is what makes the loop repeatable.
    The independence that matters is the reviewer's (`Judge`), not the
    worker's.

    `on_action` is told each thing the worker does, in words, for the row
    the engineer watches.
    """

    async def run(self, prompt: str, on_action: OnAction | None = None) -> str:
        ...


# --- resumable session state --------------------------------------------------

@dataclass
class Checkpoint:
    """Hamilton's own record of a session, written to `.hamilton/session`.

    Deliberately not a dump of adapter-internal state: `session_ref` is an
    opaque string the adapter hands back and later understands, and nothing
    else here is vendor-shaped. That is what lets a different harness pick up
    the same file.
    """
    phase: str
    session_ref: str | None = None
    turns_completed: int = 0
    last_summary: str = ""
    done: bool = False

    @property
    def resumable(self) -> bool:
        return bool(self.session_ref) and not self.done

    def save(self, root: str) -> None:
        path = os.path.join(root, SESSION_REL)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, indent=2, sort_keys=True)
            fh.write("\n")

    @classmethod
    def load(cls, root: str) -> "Checkpoint | None":
        """The stored checkpoint, or None if there is none or it is unreadable.
        A corrupt file is not an error worth stopping a session for -- it just
        means there is nothing to resume."""
        try:
            with open(os.path.join(root, SESSION_REL), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or "phase" not in data:
            return None
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def clear(cls, root: str) -> None:
        try:
            os.remove(os.path.join(root, SESSION_REL))
        except OSError:
            pass
