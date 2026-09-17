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
from typing import AsyncIterator, Callable, Protocol

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
    """The agent emitted SENTINEL: this phase's workflow is finished."""
    summary: str = ""


@dataclass(frozen=True)
class SessionError:
    message: str


Event = AgentText | ToolDenied | PhaseDone | SessionError


# --- the adapter seam ---------------------------------------------------------

class AgentAdapter(Protocol):
    """One turn in, a stream of events out.

    Construction carries the rest (project root, answerer, write policy, and
    the session reference to resume from), so this interface stays small
    enough for a future non-SDK harness to implement without inheriting any of
    today's vendor assumptions.
    """

    session_ref: str | None

    def run_turn(self, text: str) -> AsyncIterator[Event]:
        ...

    async def close(self) -> None:
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
