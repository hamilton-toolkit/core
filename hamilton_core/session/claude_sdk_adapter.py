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
    PreToolUse hook also fires (project settings are loaded so its
    conventions apply), so a write is checked twice by the same policy --
    harmless, and it keeps the hook meaningful for anything else that reads it.
  * **Foreground subagents.** An in-process PreToolUse hook refuses a subagent
    the agent asks to run in the background. (`can_use_tool` is not asked
    about the `Agent` tool, so this cannot live there.) It only covers a CLI
    that still offers that choice: this one runs every subagent as a task and
    returns the tool call at once, so what actually keeps a subagent in sight
    is `agent.Agent` holding the turn open until its task reports -- see
    `Tasks` for the lifecycle that says when it has.
  * **One-shot work.** `ClaudeSdkJudge` (a reviewer, no tools) and
    `ClaudeSdkWorker` (one step of `hamilton build`, with tools and the phase
    guard) are both `query()` calls with no session behind them. That is what
    lets the build loop be driven by Hamilton instead of by an agent. Which
    model each kind of build work runs on is decided here too
    (`DEFAULT_MODELS`, overridden by `model.<step>` in `.hamilton/config`),
    how hard it thinks (`DEFAULT_EFFORTS`, overridden by `effort.<step>`; a
    reviewer does not think at all), and what counts as a token spent
    (`_spent`).

Anything a future non-SDK harness would do differently belongs in this file.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import AsyncIterator

from claude_agent_sdk import (
    TERMINAL_TASK_STATUSES,
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

from hamilton_core import guard
from hamilton_core.session import protocol as P

# The `hamilton` skill ships in the package as a Claude Code plugin, so every
# session runs the workflows of the Hamilton that is installed -- nothing is
# copied into the project to go stale. The plugin path must be absolute, and a
# plugin's skills are named "<plugin>:<skill>".
PLUGIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "plugin")
SKILL = "hamilton:hamilton"

WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
SUBAGENT_TOOLS = ("Agent", "Task")      # "Task" is the tool's older name

FOREGROUND = ("Run subagents in the foreground; issue several Agent calls in "
              "one message to run them in parallel.")
# A build task ends when it answers: whatever it left running in the
# background never reports back, and its work is lost.
FOREGROUND_TASK = ("Run it in the foreground: this task ends when you answer, "
                   "and a background run never reports back. Several Agent "
                   "calls in one message still run in parallel.")
BACKGROUND_TOOLS = ("Bash", *SUBAGENT_TOOLS)

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


def refusal(root: str, write_policy: P.WritePolicy, tool_name: str,
            tool_input: dict) -> str | None:
    """Why this tool call may not go ahead, or None. One policy for everything
    Hamilton runs -- the session's agent and the build loop's workers alike.

    The gate governs what can be written, not what can be run or read: the
    tool surface is otherwise the agent's usual one.
    """
    if tool_name not in WRITE_TOOLS:
        return None
    target = guard.target_of(tool_input)
    if not target:
        return None
    return (write_policy(target)
            or guard.suffix_denial(tool_name, tool_input, root))


# What each of the CLI's tools does, in words: (with its target, without).
_ACTIONS = {
    "Read": ("reading {}", "reading a file"),
    "Write": ("writing {}", "writing a file"),
    "Edit": ("editing {}", "editing a file"),
    "MultiEdit": ("editing {}", "editing a file"),
    "NotebookEdit": ("editing {}", "editing a notebook"),
    "Bash": ("running {}", "running a command"),
    "Grep": ("searching for {}", "searching the code"),
    "Glob": ("looking for {}", "looking for files"),
    "WebFetch": ("fetching {}", "fetching a page"),
    "WebSearch": ("searching the web for {}", "searching the web"),
    "Agent": ("delegating: {}", "delegating to a subagent"),
    "Task": ("delegating: {}", "delegating to a subagent"),
    "TodoWrite": ("planning its steps", "planning its steps"),
}
_TARGETS = ("file_path", "notebook_path", "command", "pattern", "url", "query",
            "description")
_TARGET_WIDTH = 60


def action(root: str, tool_name: str, tool_input: dict | None = None) -> str:
    """What a tool call does, in words an engineer reads at a glance --
    "editing src/Http/EnforceHttps.php", "running tools/run-tests.sh". Paths
    inside the project are shown relative to it; a long target is cut."""
    with_target, without = _ACTIONS.get(
        tool_name, (f"using {tool_name} on {{}}", f"using {tool_name}"))
    target = next((str(tool_input[k]) for k in _TARGETS
                   if (tool_input or {}).get(k)), "")
    target = " ".join(target.split())                   # one line
    if target.startswith(root.rstrip("/") + "/"):
        target = target[len(root.rstrip("/")) + 1:]
    if len(target) > _TARGET_WIDTH:
        target = target[:_TARGET_WIDTH - 1] + "…"
    return with_target.format(target) if target else without


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
            # Load the project's .claude/ so its settings and the phase-guard
            # hook apply.
            setting_sources=["project"],
            plugins=[{"type": "local", "path": PLUGIN}],
            # Named, not "all": `skills="all"` appends a bare `Skill` to the
            # effective allowed-tools, which auto-approves the tool ahead of
            # `can_use_tool` and makes the SDK warn about a shadowed callback.
            # A session is scoped to a phase, so it wants its own skill anyway.
            skills=[SKILL],
            mcp_servers={"hamilton": create_sdk_mcp_server(
                "hamilton", tools=[ask_engineer])},
            can_use_tool=self._can_use_tool,
            hooks={"PreToolUse": [HookMatcher(matcher="|".join(SUBAGENT_TOOLS),
                                              hooks=[_foreground(FOREGROUND)])]},
            resume=resume_ref,
        )

    async def _can_use_tool(self, tool_name: str, tool_input: dict, context):
        denial = refusal(self._root, self._write_policy, tool_name, tool_input)
        if denial is None:
            return PermissionResultAllow()
        self._denials.append(P.ToolDenied(guard.target_of(tool_input) or "",
                                          denial))
        return PermissionResultDeny(message=denial)

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
        tasks = Tasks()
        async for msg in self._client.receive_messages():
            for ev in self._drain():
                yield ev
            if isinstance(msg, ResultMessage) and msg.session_id:
                self.session_ref = msg.session_id
            for ev in _translate(msg, tasks):
                yield ev

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None


class Tasks:
    """Which of the CLI's tasks are subagents Hamilton shows, and under which
    id.

    The CLI runs a subagent as a *task*: the `Agent` tool call returns as soon
    as one is launched, and the task's own lifecycle -- started, progress,
    finished -- arrives as system messages afterwards. So the tool result says
    nothing about whether the subagent is done, and the rows have to follow
    the task. Tasks the CLI runs for itself (a background command, say) are
    not the agent's subagents and are left alone.
    """

    def __init__(self) -> None:
        self._launched: set[str] = set()    # subagent tool calls, by tool id
        self._ours: set[str] = set()        # their tasks, by task id

    def launched(self, tool_use_id: str) -> None:
        self._launched.add(tool_use_id)

    def started(self, msg) -> list[P.StreamEvent]:
        if msg.tool_use_id not in self._launched:
            return []
        self._ours.add(msg.task_id)
        return [P.TaskStarted(msg.task_id, msg.description or "subagent")]

    def progress(self, msg) -> list[P.StreamEvent]:
        if msg.task_id not in self._ours:
            return []
        # the CLI reports the tool's name only, not what it was used on
        return [P.TaskProgress(msg.task_id,
                               action("", msg.last_tool_name) if msg.last_tool_name else "")]

    def ended(self, task_id: str, status: str | None) -> list[P.StreamEvent]:
        if task_id not in self._ours or status not in TERMINAL_TASK_STATUSES:
            return []
        self._ours.discard(task_id)
        return [P.TaskEnded(task_id, status == "completed")]


def _translate(msg, tasks: Tasks) -> list[P.StreamEvent]:
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
                tasks.launched(block.id)
        return out
    if isinstance(msg, TaskStartedMessage):
        return tasks.started(msg)
    if isinstance(msg, TaskProgressMessage):
        return tasks.progress(msg)
    if isinstance(msg, TaskNotificationMessage):
        return tasks.ended(msg.task_id, msg.status)
    if isinstance(msg, TaskUpdatedMessage):
        # A finished task sometimes reports only here.
        return tasks.ended(msg.task_id, msg.status)
    if isinstance(msg, ResultMessage):
        out = []
        if msg.is_error:
            out.append(P.SessionError(msg.result or msg.subtype
                                      or "the agent session failed"))
        # Ours, or one the CLI started itself to report a finished task.
        by_agent = not (msg.origin is None or msg.origin.get("kind") == "human")
        out.append(P.TurnEnded(by_agent))
        return out
    return []


def _foreground(reason: str):
    """A PreToolUse hook that refuses any call asking to run in the
    background, with `reason`. A hook, not `can_use_tool`: that is not asked
    about the `Agent` tool, nor about a command the project already allows."""
    async def hook(hook_input, tool_use_id, context) -> dict:
        if not (hook_input.get("tool_input") or {}).get("run_in_background"):
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "permissionDecision": "deny",
                                       "permissionDecisionReason": reason}}
    return hook


# The model for each kind of build work when `.hamilton/config` names none.
# Writing and reviewing tests are many small, well-briefed tasks: a mid-tier
# model does them for a fraction of the usage. Planning and coding decide
# the shape of the code, and keep the CLI's default.
DEFAULT_MODELS = {"tests": "sonnet", "review": "sonnet"}

# How hard each kind of build work thinks, where the CLI's default is too
# much. Left to itself a test writer spends most of its output thinking --
# twenty thousand tokens before a single edit, at times.
DEFAULT_EFFORTS = {"tests": "medium", "code": "medium"}

# Every build agent caches its prompt for 5 minutes, not the hour a
# subscription defaults to: a 1-hour cache write costs twice the input, a
# 5-minute one a quarter more. Each read renews it, so only a single step
# idle for longer pays a write again -- rare, and cheaper than the hour on
# every write. Caching cannot be turned off on a subscription: with the
# client's markers gone the server still caches, for 5 minutes.
CACHE = {"CLAUDE_CODE_PROMPT_CACHE_TTL": "5m"}

# A worker's command runs to its end where the worker waits for it. Left to
# itself the CLI moves a command still running after two minutes -- a
# browser test file, say -- into the background, and the task, which ends
# when it answers, either waits turn after turn or never hears back.
FOREGROUND_ENV = {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
                  "BASH_DEFAULT_TIMEOUT_MS": "900000",
                  "BASH_MAX_TIMEOUT_MS": "900000"}


def _model(step: str, chosen: dict) -> str | None:
    """The model for `step`: the engineer's choice, else ours, else None --
    the CLI's own default."""
    return chosen.get(step) or DEFAULT_MODELS.get(step)


def _effort(step: str, chosen: dict) -> str | None:
    """The effort for `step`, the way `_model` picks its model."""
    return chosen.get(step) or DEFAULT_EFFORTS.get(step)


def _spent(msg: ResultMessage) -> int:
    """The tokens one query used: everything its models read fresh and
    everything they wrote, subagents included. Reading a cached prompt again
    is left out -- it is cheap, and would count one prompt over and over."""
    if msg.model_usage:
        return sum(int(u.get(k) or 0) for u in msg.model_usage.values()
                   for k in ("inputTokens", "outputTokens", "cacheCreationInputTokens"))
    usage = msg.usage or {}
    return sum(int(usage.get(k) or 0) for k in
               ("input_tokens", "output_tokens", "cache_creation_input_tokens"))


class ClaudeSdkJudge:
    """`protocol.Judge` over `claude_agent_sdk`: every `ask` is a fresh
    one-turn session with no tools, no filesystem settings (so no project
    skill, hook or MCP server) and an empty temp dir as its cwd. The prompt is
    all it has to go on.

    It does not think: a review fills in a checklist it is given, and
    thinking was nine tenths of what a review cost. Its prompt is cached for
    the shortest time there is (`CACHE`): no later call reads it back."""

    def __init__(self, model: str | None = None,
                 effort: str | None = None) -> None:
        self._model = _model("review", {"review": model} if model else {})
        self._effort = _effort("review", {"review": effort} if effort else {})
        self.tokens: dict = {}

    def _options(self, cwd: str) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            cwd=cwd,
            tools=[],
            allowed_tools=[],
            setting_sources=[],
            strict_mcp_config=True,
            max_turns=1,
            model=self._model,
            effort=self._effort,
            thinking={"type": "disabled"},
            env=CACHE,
        )

    async def ask(self, prompt: str, on_tokens: P.OnTokens | None = None) -> str:
        texts, error = [], None
        with tempfile.TemporaryDirectory(prefix="hamilton-review-") as cwd:
            async for msg in query(prompt=prompt, options=self._options(cwd)):
                if isinstance(msg, AssistantMessage):
                    texts += [b.text for b in msg.content if isinstance(b, TextBlock)]
                elif isinstance(msg, ResultMessage):
                    spent = _spent(msg)
                    self.tokens["review"] = self.tokens.get("review", 0) + spent
                    if on_tokens:
                        on_tokens(spent)
                    if msg.is_error:
                        error = msg.result or msg.subtype or "the reviewer session failed"
        if error:
            raise RuntimeError(error)
        return "\n".join(texts)


class ClaudeSdkWorker:
    """`protocol.Worker` over `claude_agent_sdk`: one step of `hamilton
    build`, as a `query()` with tools, in the project, under the phase gate.

    No session and no resume: the prompt Hamilton built is the brief, and
    when the work is done the process is gone. That is what makes a step
    repeatable. The project's own settings are loaded, so its conventions
    and hooks apply to the work -- but not its skills: the prompt is the
    whole brief, and a skill the worker loads is read again on every turn.
    Nothing may run in the background: the task is over when it answers.
    """

    def __init__(self, root: str, write_policy: P.WritePolicy,
                 models: dict | None = None, efforts: dict | None = None) -> None:
        self._root = root
        self._write_policy = write_policy
        self._models = models or {}
        self._efforts = efforts or {}
        self.denials: list[P.ToolDenied] = []
        self.tokens: dict = {}

    async def _can_use_tool(self, tool_name: str, tool_input: dict, context):
        denial = refusal(self._root, self._write_policy, tool_name, tool_input)
        if denial is None:
            return PermissionResultAllow()
        self.denials.append(P.ToolDenied(guard.target_of(tool_input) or "",
                                         denial))
        return PermissionResultDeny(message=denial)

    def _options(self, step: str = "") -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            cwd=self._root,
            setting_sources=["project"],
            env={**CACHE, **FOREGROUND_ENV},
            skills=[],
            can_use_tool=self._can_use_tool,
            hooks={"PreToolUse": [HookMatcher(matcher="|".join(BACKGROUND_TOOLS),
                                              hooks=[_foreground(FOREGROUND_TASK)])]},
            model=_model(step, self._models),
            effort=_effort(step, self._efforts),
        )

    async def run(self, prompt: str, on_action: P.OnAction | None = None,
                  step: str = "", on_tokens: P.OnTokens | None = None) -> str:
        texts, error = [], None
        async for msg in query(prompt=prompt, options=self._options(step)):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        texts.append(block.text)
                    elif isinstance(block, ToolUseBlock) and on_action:
                        on_action(action(self._root, block.name, block.input))
            elif isinstance(msg, ResultMessage):
                spent = _spent(msg)
                self.tokens[step] = self.tokens.get(step, 0) + spent
                if on_tokens:
                    on_tokens(spent)
                if msg.is_error:
                    error = msg.result or msg.subtype or "the task failed"
        if error:
            raise RuntimeError(error)
        return "\n".join(texts)

