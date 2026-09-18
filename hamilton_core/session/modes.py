"""The session modes: `hamilton design`, `build`, `reverse`, and any added
later. A mode is everything that differs between them -- which phase it sets,
how the agent is told to start, what is offered after an iteration, and how
the session is summed up. Everything else (the console, the adapter, the
turn loop) is shared, so a new mode is one `Mode` here and nothing more.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from hamilton_core import tree


@dataclass(frozen=True)
class Step:
    """A step offered after an iteration. `instruction` is what the agent is
    sent. A step that `picks_requirements` first lets the engineer choose
    requirements from the tree and describe the change; its instruction is a
    template with `{requirements}` and `{change}`."""
    label: str
    instruction: str
    picks_requirements: bool = False


@dataclass(frozen=True)
class Mode:
    name: str                   # the CLI verb
    phase: str                  # what `.hamilton/phase` is set to
    help: str
    kickoff: str                # the opening instruction, so the agent starts
    footer: str                 # printed when the session ends
    next_steps: tuple[Step, ...]
    # Returns why the mode cannot run here, or None.
    precheck: Callable[[str], str | None] | None = None


# The kickoffs and steps only point at the `hamilton` skill, where the real
# protocol lives.

_SPEC_STEPS: tuple[Step, ...] = (
    Step("Specify another change",
         "The engineer has another change to the specification. Run the "
         "Specify review protocol from Phase 1: ask what the change is, plan "
         "it silently, state the size, then present it one item at a time and "
         "write each to `spec/` only on approval."),
    Step("Change specific requirements",
         "The engineer selected {requirements} and wants: {change}\n\nRun the "
         "Specify review protocol from Phase 1 for exactly this change. It may "
         "edit, remove or add requirements.",
         picks_requirements=True),
    Step("Decompose a requirement further",
         "The engineer wants to decompose an existing requirement into "
         "children. Render `hamilton tree`, ask which requirement to take, "
         "then run the review protocol for the new children."),
    Step("Check the tree adds up",
         "Render `hamilton tree` and read it upward: for each parent, ask "
         "whether its children add up to it. Report any gap you find, then "
         "run the review protocol for whatever the engineer decides to fix."),
)


def _spec_is_empty(root: str) -> str | None:
    """`reverse` derives a *first* spec, so it refuses a spec with content."""
    n = len(tree.rows(root))
    if not n:
        return None
    return (f"spec/requirements.md already has {n} requirement(s). `hamilton "
            f"reverse` derives a first spec from an existing codebase; it will "
            f"not run against a spec that already has content. Run `hamilton "
            f"design` to extend the existing spec.")


DESIGN = Mode(
    name="design",
    phase="spec",
    help="set phase to spec, then run the spec session",
    kickoff=(
        "Start the Hamilton spec/design session now: follow the `hamilton` "
        "skill's Specify workflow from the top -- greet me, summarise the "
        "current spec state; if the spec is empty and `spec/vision.md` is still "
        "the scaffold, offer to help me draft the vision first, then move on to "
        "the root requirements; otherwise ask whether I want to draft the "
        "initial spec or modify/extend existing requirements. Then run the "
        "review protocol."
    ),
    footer=("hamilton design: session ended (phase 'spec'). Run `hamilton "
            "build` to implement the changes, or `hamilton design` again to "
            "keep specifying."),
    next_steps=_SPEC_STEPS,
)

BUILD = Mode(
    name="build",
    phase="build",
    help="set phase to build, then run the build session",
    kickoff=(
        "Start the Hamilton build session now: follow the `hamilton` skill's "
        "\"Propagate a change\" / \"Verify\" workflow immediately -- run "
        "`git diff spec/` and `hamilton check`, bring the code and tests to "
        "green, then give the closing summary. If this is the first build after "
        "`hamilton reverse` (no `.hamilton/verified`, most ACs uncovered, the "
        "spec only just landed in `git log -- spec`), follow \"Adopt an "
        "existing test suite\" instead. Do not wait for further instruction."
    ),
    footer=("hamilton build: session ended (phase 'build'). Run `hamilton "
            "check` to confirm the gate is green before opening a merge "
            "request."),
    next_steps=(
        Step("Take another build task",
             "The engineer has more for you to build. Ask what it is, then "
             "follow the Implement / Propagate a change workflow and get "
             "`hamilton check` green."),
        Step("Re-run the gate",
             "Run `hamilton check` again and report what it says. If it is "
             "red, follow the Verify workflow until it is green."),
    ),
)

REVERSE = Mode(
    name="reverse",
    phase="spec",
    help="brownfield: set phase to spec, then run a session that derives a "
         "first spec from an existing codebase",
    kickoff=(
        "Start the Hamilton reverse (brownfield) session now: follow the "
        "`hamilton` skill's \"Reverse-engineer the spec from existing code\" "
        "workflow from the top -- survey the codebase and its git history, "
        "show me the frame you infer (what the system is for, its actors, the "
        "module map) and let me correct it, draft `spec/vision.md`, then derive "
        "the requirement tree module by module. Propose every piece and wait "
        "for my approval before writing it -- the spec captures intent and the "
        "load-bearing decisions, it does not restate the code."
    ),
    footer=("hamilton reverse: session ended (phase 'spec'). `hamilton check` "
            "will be red on `uncovered` until you run `hamilton build` -- that "
            "session binds the existing tests to the derived criteria. Run "
            "`hamilton design` to keep refining the spec."),
    next_steps=_SPEC_STEPS,
    precheck=_spec_is_empty,
)

MODES: dict[str, Mode] = {m.name: m for m in (DESIGN, BUILD, REVERSE)}
