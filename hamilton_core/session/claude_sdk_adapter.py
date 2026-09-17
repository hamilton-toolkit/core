"""The only module in Hamilton that imports `claude_agent_sdk`.

It implements `protocol.AgentAdapter` by translating the SDK's message stream
into Hamilton's events, and nothing else. Two vendor-specific things are
contained here on purpose:

  * **Questions.** The engineer-facing question flow is an in-process MCP tool
    (`ask_engineer`) rather than the CLI's own interactive prompt, because
    Hamilton has to own the rendering to offer "re-pick before this is sent".
    The skill is told to call it.
  * **Writes.** The phase gate runs as a `can_use_tool` callback over
    `hamilton_core.guard.decide`. The project's `.claude/settings.json`
    PreToolUse hook also fires (project settings are loaded so the `hamilton`
    skill is available), so a write is checked twice by the same policy --
    harmless, and it keeps the hook meaningful for anything else that reads it.

Anything a future non-SDK harness would do differently belongs in this file.
"""

from __future__ import annotations

import asyncio
import re
from typing import AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    create_sdk_mcp_server,
    tool,
)

from hamilton_core.session import protocol as P

WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")

_ASK_DESCRIPTION = (
    "Ask the engineer a question and wait for their answer. Use this for every "
    "question you put to the engineer -- approval of a proposed requirement, a "
    "decomposition choice, an open input boundary. Supply `choices` when the "
    "answer is a selection; leave it empty for an open question. Hamilton "
    "always adds 'Type my own answer' and 'Finish this session' rows itself, "
    "so do not include a catch-all choice such as 'Other' or 'Something else'."
)

# Catch-all choices duplicate Hamilton's own "Type my own answer" row. The
# description asks the model not to send them; this drops any that slip through.
_CATCH_ALL = re.compile(
    r"^\W*(other|something else|type my own|none of these)\b", re.I)

_ASK_SCHEMA = {
    "prompt": str,
    "header": str,
    "choices": list,
}


def _target_of(tool_input: dict) -> str | None:
    return tool_input.get("file_path") or tool_input.get("notebook_path")


def _as_question(args: dict) -> P.Question:
    choices = []
    for c in args.get("choices") or ():
        if isinstance(c, dict):
            choices.append(P.Choice(str(c.get("label", "")),
                                    str(c.get("description", ""))))
        else:
            choices.append(P.Choice(str(c)))
    choices = [c for c in choices if not _CATCH_ALL.match(c.label)]
    return P.Question(prompt=str(args.get("prompt", "")),
                      choices=tuple(choices),
                      header=str(args.get("header", "")))


class ClaudeSdkAdapter:
    """`protocol.AgentAdapter` over `claude_agent_sdk`."""

    def __init__(self, root: str, answerer: P.Answerer,
                 write_policy: P.WritePolicy,
                 resume_ref: str | None = None,
                 model: str | None = None) -> None:
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
            resume=resume_ref,
            model=model,
        )

    async def _can_use_tool(self, tool_name: str, tool_input: dict, context):
        if tool_name in WRITE_TOOLS:
            target = _target_of(tool_input)
            if target:
                denial = self._write_policy(target)
                if denial is not None:
                    self._denials.append(P.ToolDenied(target, denial))
                    return PermissionResultDeny(message=denial)
        # The gate governs what can be written, not what can be run or read:
        # the agent's tool surface is otherwise its usual one.
        return PermissionResultAllow()

    def _drain(self) -> list[P.Event]:
        out, self._denials = list(self._denials), []
        return out

    async def _ensure_connected(self) -> None:
        if self._client is None:
            self._client = ClaudeSDKClient(options=self._options)
            await self._client.connect()

    async def run_turn(self, text: str) -> AsyncIterator[P.Event]:
        await self._ensure_connected()
        assert self._client is not None
        await self._client.query(text)

        saw_sentinel = False
        tail = ""
        async for msg in self._client.receive_response():
            for ev in self._drain():
                yield ev
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        body, hit = _strip_sentinel(block.text)
                        saw_sentinel = saw_sentinel or hit
                        if body.strip():
                            tail = body
                            yield P.AgentText(body)
            elif isinstance(msg, ResultMessage):
                if msg.session_id:
                    self.session_ref = msg.session_id
                if msg.is_error:
                    yield P.SessionError(msg.result or msg.subtype
                                         or "the agent session failed")

        for ev in self._drain():
            yield ev
        if saw_sentinel:
            yield P.PhaseDone(tail.strip())

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None


def _strip_sentinel(text: str) -> tuple[str, bool]:
    """Text with the sentinel line removed, and whether it was there. The
    engineer should see the closing summary, not the marker that ends it."""
    if P.SENTINEL not in text:
        return text, False
    kept = [ln for ln in text.splitlines() if ln.strip() != P.SENTINEL]
    return "\n".join(kept), True
