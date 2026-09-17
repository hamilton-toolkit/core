"""The agent session behind `hamilton design` / `build` / `reverse`.

Hamilton drives the session turn by turn rather than handing the engineer a
terminal, which is what makes the question/re-pick flow, phase auto-exit and
mid-session resume possible at all.

Layering, and why it matters: `protocol` is Hamilton's own vocabulary and
imports no vendor SDK; `loop` (the terminal renderer and session driver) is
written against `protocol` alone. `claude_sdk_adapter` is the *only* module
that imports `claude_agent_sdk`. Supporting a different model, or replacing the
SDK with a hand-rolled harness, therefore means writing one new `AgentAdapter`
-- the engineer-facing behaviour does not move, and does not need
re-validating.
"""
