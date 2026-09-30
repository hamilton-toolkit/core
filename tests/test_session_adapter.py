"""The Claude Agent SDK adapter -- the one module that imports the SDK.

Nothing here connects to a model: what is worth pinning is the translation in
both directions -- SDK tool arguments into a Hamilton `Question`, Hamilton's
phase policy into an SDK permission result, and SDK messages into Hamilton
events.
"""

import asyncio
import os

from claude_agent_sdk import (
    AssistantMessage, ResultMessage, TaskNotificationMessage,
    TaskProgressMessage, TaskStartedMessage, TaskUpdatedMessage, TextBlock,
    ToolResultBlock, ToolUseBlock, UserMessage,
)

from hamilton_core.session import protocol as P
from hamilton_core.session.claude_sdk_adapter import (
    FOREGROUND, ClaudeSdkAdapter, ClaudeSdkJudge, ClaudeSdkWorker, Tasks,
    _as_question, FOREGROUND_TASK, _spent, _translate,
)


def adapter(answerer=None, write_policy=None, **kw):
    return ClaudeSdkAdapter(root="/tmp",
                            answerer=answerer or (lambda q: "ok"),
                            write_policy=write_policy or (lambda p: None), **kw)


# --- session options ---------------------------------------------------------

def test_the_hamilton_skill_loads_from_the_installed_package():
    """Not from a copy in the project, which would go stale on the next
    Hamilton: the package ships it as a plugin."""
    import json
    import os

    from hamilton_core.session.claude_sdk_adapter import PLUGIN, SKILL
    o = adapter()._options
    assert o.setting_sources == ["project"]
    assert o.plugins == [{"type": "local", "path": PLUGIN}]
    assert os.path.isabs(PLUGIN)                  # a relative one is not loaded
    assert o.skills == [SKILL]
    assert "hamilton" in o.mcp_servers
    # a plugin's skill is named "<plugin>:<skill>"
    plugin, skill = SKILL.split(":")
    with open(os.path.join(PLUGIN, ".claude-plugin", "plugin.json")) as fh:
        assert json.load(fh)["name"] == plugin
    with open(os.path.join(PLUGIN, "skills", skill, "SKILL.md")) as fh:
        assert f"\nname: {skill}\n" in fh.read()


def test_the_permission_callback_is_not_shadowed():
    """`skills="all"` appends a bare `Skill` to the effective allowed-tools,
    which auto-approves it ahead of `can_use_tool` -- the SDK warns about the
    shadowed callback, and the warning surfaces in the engineer's terminal.
    Naming the skill avoids it, so the write gate is consulted for every tool
    the gate cares about."""
    import warnings

    from claude_agent_sdk.types import _warn_if_can_use_tool_shadowed

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _warn_if_can_use_tool_shadowed(adapter()._options)
    assert caught == [], [str(w.message) for w in caught]


def test_a_resume_ref_is_passed_through_and_reported():
    a = adapter(resume_ref="sess-123")
    assert a._options.resume == "sess-123"
    assert a.session_ref == "sess-123"


# --- the question bridge -----------------------------------------------------

def test_tool_arguments_become_a_question_and_the_answer_goes_back():
    seen = []
    a = adapter(answerer=lambda q: (seen.append(q), "R-0007")[1])
    result = asyncio.run(a._ask_tool.handler(
        {"prompt": "Which parent?", "header": "Parent",
         "choices": [{"label": "R-0007", "description": "Sessions"}]}))
    assert result == {"content": [{"type": "text", "text": "R-0007"}]}
    assert seen[0] == P.Question("Which parent?",
                                 (P.Choice("R-0007", "Sessions"),), "Parent")


def test_choices_may_be_bare_strings_or_absent():
    assert _as_question({"prompt": "p", "choices": ["A", "B"]}).choices == \
        (P.Choice("A"), P.Choice("B"))
    assert _as_question({"prompt": "p"}).choices == ()


# --- the phase gate, as an SDK permission result ----------------------------

def test_a_forbidden_write_is_denied_with_the_policy_message():
    a = adapter(write_policy=lambda p: "phase is 'spec'" if p == "src/x.py" else None)

    async def go():
        allow = await a._can_use_tool("Write", {"file_path": "spec/r.md"}, None)
        deny = await a._can_use_tool("Edit", {"file_path": "src/x.py"}, None)
        other = await a._can_use_tool("Bash", {"command": "ls"}, None)
        return allow, deny, other

    allow, deny, other = asyncio.run(go())
    assert allow.behavior == "allow"
    assert deny.behavior == "deny" and deny.message == "phase is 'spec'"
    assert other.behavior == "allow"   # the gate covers writes, not the world
    assert a._drain() == [P.ToolDenied("src/x.py", "phase is 'spec'")]
    assert a._drain() == []            # drained once, then reported


def test_a_notebook_write_is_gated_on_its_own_path_field():
    a = adapter(write_policy=lambda p: f"no: {p}")
    r = asyncio.run(a._can_use_tool("NotebookEdit", {"notebook_path": "n.ipynb"}, None))
    assert r.behavior == "deny" and "n.ipynb" in r.message


def test_a_forged_review_suffix_is_denied(tmp_path):
    (tmp_path / ".hamilton").mkdir()
    (tmp_path / ".hamilton" / "phase").write_text("build")
    a = ClaudeSdkAdapter(root=str(tmp_path), answerer=lambda q: "ok",
                         write_policy=lambda p: None)
    forged = {"file_path": "tests/a.js",
              "content": "// @covers R-0001/AC1 #aaaaaa.bbbbbb\n"}
    r = asyncio.run(a._can_use_tool("Write", forged, None))
    assert r.behavior == "deny" and "hamilton build" in r.message


# --- the reviewer ------------------------------------------------------------

def test_the_judge_session_has_no_tools_settings_or_history():
    o = ClaudeSdkJudge()._options("/tmp/empty")
    assert o.tools == [] and o.allowed_tools == []
    assert o.setting_sources == [] and o.strict_mcp_config is True
    assert o.mcp_servers == {} and o.resume is None
    assert o.max_turns == 1 and o.cwd == "/tmp/empty"


def test_the_judge_does_not_think():
    assert ClaudeSdkJudge()._options("/tmp/empty").thinking == {"type": "disabled"}


def test_every_build_agent_caches_for_five_minutes():
    ttl = ("CLAUDE_CODE_PROMPT_CACHE_TTL", "5m")
    assert ttl in ClaudeSdkJudge()._options("/tmp/empty").env.items()
    w = ClaudeSdkWorker("/tmp/p", write_policy=lambda p: None)
    assert all(ttl in w._options(step).env.items() for step in ("plan", "tests", "code"))


def test_a_worker_s_long_command_is_not_moved_to_the_background():
    """The CLI does that after two minutes; a browser test file takes longer."""
    env = ClaudeSdkWorker("/tmp/p", write_policy=lambda p: None)._options("code").env
    assert env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
    assert int(env["BASH_DEFAULT_TIMEOUT_MS"]) >= 600_000


# --- the message stream, as Hamilton events --------------------------------

def said(text, parent=None):
    return AssistantMessage([TextBlock(text)], "m", parent_tool_use_id=parent)


def called(name, id, parent=None, **input):
    return AssistantMessage([ToolUseBlock(id, name, input)], "m",
                            parent_tool_use_id=parent)


def returned(id, is_error=None, parent=None):
    return UserMessage([ToolResultBlock(id, "done", is_error)],
                       parent_tool_use_id=parent)


def result(origin=None, is_error=False, text="ok"):
    return ResultMessage("success", 1, 1, is_error, 1, "sess-9", result=text,
                         origin=origin)


def progress(task_id, tool_uses, last_tool, tool_use_id="X"):
    return TaskProgressMessage(
        "task_progress", {}, task_id=task_id, description="running tests",
        usage={"total_tokens": 1, "tool_uses": tool_uses, "duration_ms": 1},
        uuid="u", session_id="s", tool_use_id=tool_use_id, last_tool_name=last_tool)


def task_started(task_id, tool_use_id, description="Write test R-0001/AC1"):
    return TaskStartedMessage("task_started", {}, task_id=task_id,
                              description=description, uuid="u", session_id="s",
                              tool_use_id=tool_use_id)


def notified(task_id, status):
    return TaskNotificationMessage("task_notification", {}, task_id=task_id,
                                   status=status, output_file="/tmp/o",
                                   summary="done", uuid="u", session_id="s")


def updated(task_id, status):
    return TaskUpdatedMessage("task_updated", {}, task_id=task_id,
                              patch={"status": status}, status=status)


def translate(*messages):
    """The events a fresh stream makes of `messages`, in order."""
    tasks = Tasks()
    return [ev for msg in messages for ev in _translate(msg, tasks)]


def test_the_main_agents_text_is_shown():
    assert translate(said("Tests are green.")) == [P.AgentText("Tests are green.")]


def test_a_subagents_text_and_calls_are_not():
    assert translate(said("thinking aloud", parent="X")) == []
    assert translate(called("Bash", "b1", parent="X", command="ls")) == []
    assert translate(UserMessage("the subagent's prompt", parent_tool_use_id="X")) == []


def test_a_subagent_is_a_row_from_its_task_starting_to_its_task_finishing():
    """The `Agent` call returns as soon as the subagent is launched, so its
    tool result says nothing about whether the work is done -- only the task's
    own lifecycle does."""
    assert translate(
        called("Agent", "X", description="Write test R-0001/AC1", prompt="..."),
        task_started("t1", "X"),
        progress("t1", 3, "Edit"),
        notified("t1", "completed"),
    ) == [P.TaskStarted("t1", "Write test R-0001/AC1"),
          P.TaskProgress("t1", "editing a file"),
          P.TaskEnded("t1", True)]


def test_the_tool_call_returning_does_not_close_the_row():
    assert translate(called("Agent", "X", description="Write test", prompt="..."),
                     task_started("t1", "X", "Write test"),
                     returned("X")) == [P.TaskStarted("t1", "Write test")]


def test_a_task_that_only_reports_its_end_as_an_update_still_closes():
    assert translate(called("Agent", "X", description="Write test", prompt="..."),
                     task_started("t1", "X", "Write test"),
                     updated("t1", "killed")) == [P.TaskStarted("t1", "Write test"),
                                                  P.TaskEnded("t1", False)]
    # a patch that changes something else leaves the row open
    assert translate(called("Agent", "Y", description="Write test", prompt="..."),
                     task_started("t2", "Y", "Write test"),
                     updated("t2", "running")) == [P.TaskStarted("t2", "Write test")]


def test_a_failed_subagent_is_reported_as_one():
    for status in ("failed", "stopped"):
        assert translate(called("Agent", "X", description="W", prompt="..."),
                         task_started("t1", "X"),
                         notified("t1", status))[-1] == P.TaskEnded("t1", False)


def test_the_clis_own_tasks_are_not_the_agents_subagents():
    """A background command the CLI runs is a task too; it is not a subagent
    and does not hold the turn open."""
    assert translate(called("Bash", "b2", command="npm test"),
                     task_started("t9", "b2", description="npm test"),
                     progress("t9", 1, "Bash"),
                     notified("t9", "completed")) == []


def test_every_turns_result_is_reported_and_whose_it_was():
    assert translate(result()) == [P.TurnEnded(False)]
    assert translate(result(origin={"kind": "human"})) == [P.TurnEnded(False)]
    assert translate(result(origin={"kind": "task-notification"})) == [
        P.TurnEnded(True)]


def test_a_failed_result_is_a_session_error():
    assert translate(result(is_error=True, text="out of budget")) == [
        P.SessionError("out of budget"), P.TurnEnded(False)]


def test_a_turn_s_end_carries_what_it_spent():
    spent = ResultMessage("success", 1, 1, False, 1, "s",
                          usage={"input_tokens": 10, "output_tokens": 5,
                                 "cache_creation_input_tokens": 100})
    assert translate(spent) == [P.TurnEnded(False, 115)]


def foreground_hook(options, tool_name):
    """The first PreToolUse hook `options` runs for `tool_name`, as a
    callable."""
    matcher = next(m for m in options.hooks["PreToolUse"]
                   if tool_name in m.matcher.split("|"))
    hook = matcher.hooks[0]

    def call(**tool_input):
        return asyncio.run(hook({"tool_name": tool_name, "tool_input": tool_input},
                                "X", None))
    return call


def test_the_foreground_hook_is_installed_for_the_subagent_tool():
    [matcher] = adapter()._options.hooks["PreToolUse"]
    assert matcher.matcher == "Agent|Task"


def test_a_background_subagent_is_refused_and_a_foreground_one_allowed():
    hook = foreground_hook(adapter()._options, "Agent")

    assert hook(description="d", prompt="p") == {}
    assert hook(description="d", prompt="p", run_in_background=False) == {}
    denied = hook(description="d", prompt="p", run_in_background=True)
    assert denied["hookSpecificOutput"] == {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": FOREGROUND}


def test_the_stream_carries_denials_and_records_the_session_ref():
    class Client:
        async def receive_messages(self):
            yield said("hi")
            yield result()

    async def collect(a):
        return [ev async for ev in a.events()]

    a = adapter()
    a._client = Client()
    a._denials.append(P.ToolDenied("spec/r.md", "phase is 'build'"))
    assert asyncio.run(collect(a)) == [P.ToolDenied("spec/r.md", "phase is 'build'"),
                                       P.AgentText("hi"), P.TurnEnded()]
    assert a.session_ref == "sess-9"


# --- the worker: one step of `hamilton build` --------------------------------

def test_a_worker_runs_in_the_project_under_the_phase_gate():
    w = ClaudeSdkWorker("/tmp/p", write_policy=lambda p: f"no: {p}")
    assert w._options().cwd == "/tmp/p"
    # every task works under the project's own conventions and hooks
    assert w._options().setting_sources == ["project"]
    r = asyncio.run(w._can_use_tool("Write", {"file_path": "spec/r.md"}, None))
    assert r.behavior == "deny" and "spec/r.md" in r.message
    assert w.denials == [P.ToolDenied("spec/r.md", "no: spec/r.md")]
    allowed = asyncio.run(w._can_use_tool("Bash", {"command": "ls"}, None))
    assert allowed.behavior == "allow"


def test_one_write_policy_serves_the_session_and_the_workers():
    """The gate is the same whoever is writing; only the caller differs."""
    from hamilton_core.session.claude_sdk_adapter import refusal
    policy = lambda p: "phase is 'spec'" if p.startswith("src/") else None
    assert refusal("/tmp", policy, "Edit", {"file_path": "src/x.py"}) == \
        "phase is 'spec'"
    assert refusal("/tmp", policy, "Edit", {"file_path": "tests/x.py"}) is None
    assert refusal("/tmp", policy, "Bash", {"command": "rm -rf src"}) is None



# --- what a running task is doing, in words ------------------------------------

def test_a_tool_call_reads_as_what_it_does_to_what():
    from hamilton_core.session.claude_sdk_adapter import action
    root = "/home/x/proj"
    assert action(root, "Edit", {"file_path": "/home/x/proj/src/Http/EnforceHttps.php"}) \
        == "editing src/Http/EnforceHttps.php"
    assert action(root, "Bash", {"command": "tools/run-tests.sh\n  --filter x"}) \
        == "running tools/run-tests.sh --filter x"
    assert action(root, "Read") == "reading a file"            # no target known
    assert action(root, "SomethingNew", {}) == "using SomethingNew"
    long = action(root, "Bash", {"command": "x" * 200})
    assert long.endswith("…") and len(long) < 80


# --- which model, and what it used -------------------------------------------

def test_a_worker_may_run_nothing_in_the_background():
    """Its task ends when it answers: a background run would never report."""
    options = ClaudeSdkWorker("/tmp/p", write_policy=lambda p: None)._options("code")
    for tool in ("Bash", "Agent", "Task"):
        hook = foreground_hook(options, tool)
        assert hook(command="pytest") == {}
        denied = hook(command="pytest", run_in_background=True)
        assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert denied["hookSpecificOutput"]["permissionDecisionReason"] == FOREGROUND_TASK


def test_a_worker_may_not_run_the_whole_suite(tmp_path):
    os.makedirs(tmp_path / ".hamilton")
    (tmp_path / ".hamilton" / "config").write_text("test_command=tools/run-tests.sh\n")
    options = ClaudeSdkWorker(str(tmp_path), write_policy=lambda p: None)._options("code")
    [hook] = [h for m in options.hooks["PreToolUse"] if m.matcher == "Bash"
              for h in m.hooks]

    def run(command):
        out = asyncio.run(hook({"tool_name": "Bash", "tool_input": {"command": command}},
                               "X", None))
        return (out.get("hookSpecificOutput") or {}).get("permissionDecision")

    for whole in ("tools/run-tests.sh", "./tools/run-tests.sh",
                  "timeout 590 tools/run-tests.sh > /tmp/log 2>&1; echo $?",
                  "tools/run-tests.sh 2>&1 | tail -40", "(tools/run-tests.sh)"):
        assert run(whole) == "deny", whole
    for narrowed in ("tools/run-tests.sh tests/Browser/a.test.js",
                     "cat tools/run-tests.sh.bak", "hamilton verify R-0001/AC1",
                     "tools/run-browser.sh tests/Browser/a.test.js"):
        assert run(narrowed) is None, narrowed


def test_tests_and_review_default_to_a_mid_tier_model():
    w = ClaudeSdkWorker("/tmp/p", write_policy=lambda p: None)
    assert w._options("tests").model == "sonnet"
    assert w._options("plan").model is None and w._options("code").model is None
    assert ClaudeSdkJudge()._options("/tmp/e").model == "sonnet"


def test_a_worker_loads_no_skill():
    w = ClaudeSdkWorker("/tmp/p", write_policy=lambda p: None)
    assert all(w._options(step).skills == [] for step in ("plan", "tests", "code"))


def test_a_worker_is_offered_only_the_tools_a_build_task_needs():
    """Every tool offered is sent on every turn: a task gets files and a
    shell, nothing more."""
    w = ClaudeSdkWorker("/tmp/p", write_policy=lambda p: None)
    for step in ("plan", "tests", "code", "clarify"):
        assert w._options(step).tools == ["Bash", "Read", "Write", "Edit"]


def test_writers_and_coders_think_less_than_the_cli_default():
    w = ClaudeSdkWorker("/tmp/p", write_policy=lambda p: None)
    assert w._options("tests").effort == "medium"
    assert w._options("code").effort == "medium"
    assert w._options("plan").effort == "medium"
    assert w._options("clarify").effort is None
    assert ClaudeSdkJudge()._options("/tmp/e").effort is None


def test_an_effort_named_in_the_config_wins():
    w = ClaudeSdkWorker("/tmp/p", write_policy=lambda p: None,
                        efforts={"code": "high", "clarify": "low"})
    assert w._options("code").effort == "high"
    assert w._options("clarify").effort == "low"
    assert w._options("tests").effort == "medium"
    assert ClaudeSdkJudge(effort="low")._options("/tmp/e").effort == "low"


def test_a_model_named_in_the_config_wins():
    w = ClaudeSdkWorker("/tmp/p", write_policy=lambda p: None,
                        models={"tests": "opus", "code": "sonnet"})
    assert w._options("tests").model == "opus"
    assert w._options("code").model == "sonnet"
    assert ClaudeSdkJudge("haiku")._options("/tmp/e").model == "haiku"


def result_with(usage=None, model_usage=None):
    return ResultMessage("success", 1, 1, False, 1, "s", usage=usage,
                         model_usage=model_usage)


def test_tokens_are_what_was_read_fresh_and_written():
    assert _spent(result_with(usage={"input_tokens": 10, "output_tokens": 5,
                                     "cache_creation_input_tokens": 100,
                                     "cache_read_input_tokens": 9999})) == 115


def test_tokens_include_every_model_a_query_used():
    """A worker's subagents and helper calls run on other models."""
    per_model = {"a": {"inputTokens": 10, "outputTokens": 5,
                       "cacheCreationInputTokens": 100, "cacheReadInputTokens": 9999},
                 "b": {"inputTokens": 1, "outputTokens": 2,
                       "cacheCreationInputTokens": 0, "cacheReadInputTokens": 0}}
    assert _spent(result_with(usage={"input_tokens": 10}, model_usage=per_model)) == 118
