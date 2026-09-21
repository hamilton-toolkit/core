"""The only module in Hamilton that imports `claude_agent_sdk`.

It implements `protocol.AgentAdapter` by translating the SDK's message stream
into Hamilton's events (`_translate`), and `protocol.Judge` as a one-shot
session with no tools. Turns, subagent rows and the completion sentinel are
not decided here but in `agent.Agent`, for every vendor alike. Three
vendor-specific things are contained here on purpose:

  * **Questions.** The engineer-facing question flow is an in-process MCP tool
    (`ask_engineer`) rather than the CLI's own interactive prompt, because
    Hamilton has to own the rendering to offer "re-pick before this is sent".
    The skill is told to call it.
  * **Writes.** The phase gate and the review-suffix rule run as a
    `can_use_tool` callback over `hamilton_core.guard.decide` and
    `guard.suffix_denial`. The project's `.claude/settings.json`
    PreToolUse hook also fires (project settings are loaded so the `hamilton`
    skill is available), so a write is checked twice by the same policy --
    harmless, and it keeps the hook meaningful for anything else that reads it.
  * **Foreground subagents.** An in-process PreToolUse hook refuses a
    subagent launched in the background: the turn would end while it still
    works, and the engineer would lose sight of it. (`can_use_tool` is not
    asked about the `Agent` tool, so this cannot live there.)

Anything a future non-SDK harness would do differently belongs in this file.
"""

from __future__ import annotations

import asyncio
import tempfile
from typing import AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TaskProgressMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    query,
    tool,
)

from hamilton_core import guard
from hamilton_core.session import protocol as P

WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
SUBAGENT_TOOLS = ("Agent", "Task")      # "Task" is the tool's older name

FOREGROUND = ("Run subagents in the foreground; issue several Agent calls in "
              "one message to run them in parallel.")

_ASK_DESCRIPTION = (
    "Ask the engineer a question and wait for their answer. Use this for every "
    "question you put to the engineer -- approval of a proposed requirement, a "
    "decomposition choice, an open input boundary. Supply `choices` when the "
    "answer is a selection; leave it empty for an open question. Hamilton "
    "always adds 'Type my own answer' and 'Finish this session' rows itself, "
    "so do not include a catch-all choice such as 'Other' or 'Something else', "
    "or a choice to exit or end the session."
)

_ASK_SCHEMA = {
    "prompt": str,
    "header": str,
    "choices": list,
}


def _as_question(args: dict) -> P.Question:
    choices = []
    for c in args.get("choices") or ():
        if isinstance(c, dict):
            choices.append(P.Choice(str(c.get("label", "")),
                                    str(c.get("description", ""))))
        else:
            choices.append(P.Choice(str(c)))
    return P.Question(prompt=str(args.get("prompt", "")),
                      choices=tuple(choices),
                      header=str(args.get("header", "")))


class ClaudeSdkAdapter:
    """`protocol.AgentAdapter` over `claude_agent_sdk`."""

    def __init__(self, root: str, answerer: P.Answerer,
                 write_policy: P.WritePolicy,
                 resume_ref: str | None = None) -> None:
        self._root = root
        self._answerer = answerer
        self._write_policy = write_policy
        self.session_ref = resume_ref
        self._denials: list[P.ToolDenied] = []
        self._client: ClaudeSDKClient | None = None

        @tool("ask_engineer", _ASK_DESCRIPTION, _ASK_SCHEMA)
        async def ask_engineer(args: dict) -> dict:
            # The answerer blocks on terminal input; keep the event loop free
            # so the SDK transport stays responsive while the engineer thinks.
            answer = await asyncio.to_thread(self._answerer, _as_question(args))
            return {"content": [{"type": "text", "text": answer}]}

        self._ask_tool = ask_engineer  # the SDK/Hamilton question bridge
        self._options = ClaudeAgentOptions(
            cwd=root,
            # Load the project's .claude/ so the `hamilton` skill and the
            # phase-guard hook settings apply.
            setting_sources=["project"],
            # Named, not "all": `skills="all"` appends a bare `Skill` to the
            # effective allowed-tools, which auto-approves the tool ahead of
            # `can_use_tool` and makes the SDK warn about a shadowed callback.
            # A session is scoped to a phase, so it wants its own skill anyway.
            skills=["hamilton"],
            mcp_servers={"hamilton": create_sdk_mcp_server(
                "hamilton", tools=[ask_engineer])},
            can_use_tool=self._can_use_tool,
            hooks={"PreToolUse": [HookMatcher(matcher="|".join(SUBAGENT_TOOLS),
                                              hooks=[_foreground_only])]},
            resume=resume_ref,
        )

    async def _can_use_tool(self, tool_name: str, tool_input: dict, context):
        if tool_name in WRITE_TOOLS:
            target = guard.target_of(tool_input)
            if target:
                denial = (self._write_policy(target)
                          or guard.suffix_denial(tool_name, tool_input, self._root))
                if denial is not None:
                    self._denials.append(P.ToolDenied(target, denial))
                    return PermissionResultDeny(message=denial)
        # The gate governs what can be written, not what can be run or read:
        # the agent's tool surface is otherwise its usual one.
        return PermissionResultAllow()

    def _drain(self) -> list[P.Event]:
        out, self._denials = list(self._denials), []
        return out

    async def connect(self) -> None:
        self._client = ClaudeSDKClient(options=self._options)
        await self._client.connect()

    async def send(self, text: str) -> None:
        assert self._client is not None
        await self._client.query(text)

    async def events(self) -> AsyncIterator[P.StreamEvent]:
        assert self._client is not None
        async for msg in self._client.receive_messages():
            for ev in self._drain():
                yield ev
            if isinstance(msg, ResultMessage) and msg.session_id:
                self.session_ref = msg.session_id
            for ev in _translate(msg):
                yield ev

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None


def _translate(msg) -> list[P.StreamEvent]:
    """One SDK message as Hamilton events. Only the main agent speaks to the
    engineer: a message with a `parent_tool_use_id` is a subagent's, and of
    those only its progress shows."""
    if isinstance(msg, AssistantMessage):
        if msg.parent_tool_use_id:
            return []
        out: list[P.StreamEvent] = []
        for block in msg.content:
            if isinstance(block, TextBlock) and block.text.strip():
                out.append(P.AgentText(block.text))
            elif isinstance(block, ToolUseBlock) and block.name in SUBAGENT_TOOLS:
                label = str(block.input.get("description") or "subagent")
                out.append(P.TaskStarted(block.id, label))
        return out
    if isinstance(msg, UserMessage):
        if msg.parent_tool_use_id or not isinstance(msg.content, list):
            return []
        return [P.TaskEnded(b.tool_use_id, not b.is_error)
                for b in msg.content if isinstance(b, ToolResultBlock)]
    if isinstance(msg, TaskProgressMessage):
        if not msg.tool_use_id:
            return []
        return [P.TaskProgress(msg.tool_use_id, int(msg.usage.get("tool_uses", 0)),
                               msg.last_tool_name or "")]
    if isinstance(msg, ResultMessage):
        out = []
        if msg.is_error:
            out.append(P.SessionError(msg.result or msg.subtype
                                      or "the agent session failed"))
        # Our own turn's result; not one the CLI started itself, e.g. to
        # report a finished background task.
        if msg.origin is None or msg.origin.get("kind") == "human":
            out.append(P.TurnEnded())
        return out
    return []


async def _foreground_only(hook_input, tool_use_id, context) -> dict:
    if not (hook_input.get("tool_input") or {}).get("run_in_background"):
        return {}
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "deny",
                                   "permissionDecisionReason": FOREGROUND}}


class ClaudeSdkJudge:
    """`protocol.Judge` over `claude_agent_sdk`: every `ask` is a fresh
    one-turn session with no tools, no filesystem settings (so no project
    skill, hook or MCP server) and an empty temp dir as its cwd. The prompt is
    all it has to go on."""

    def _options(self, cwd: str) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            cwd=cwd,
            tools=[],
            allowed_tools=[],
            setting_sources=[],
            strict_mcp_config=True,
            max_turns=1,
        )

    async def ask(self, prompt: str) -> str:
        texts, error = [], None
        with tempfile.TemporaryDirectory(prefix="hamilton-review-") as cwd:
            async for msg in query(prompt=prompt, options=self._options(cwd)):
                if isinstance(msg, AssistantMessage):
                    texts += [b.text for b in msg.content if isinstance(b, TextBlock)]
                elif isinstance(msg, ResultMessage) and msg.is_error:
                    error = msg.result or msg.subtype or "the reviewer session failed"
        if error:
            raise RuntimeError(error)
        return "\n".join(texts)

