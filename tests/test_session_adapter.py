"""The Claude Agent SDK adapter -- the one module that imports the SDK.

Nothing here connects to a model: what is worth pinning is the translation in
both directions -- SDK tool arguments into a Hamilton `Question`, Hamilton's
phase policy into an SDK permission result, and SDK messages into Hamilton
events.
"""

import asyncio

from claude_agent_sdk import (
    AssistantMessage, ResultMessage, TaskProgressMessage, TaskStartedMessage,
    TextBlock, ToolResultBlock, ToolUseBlock, UserMessage,
)

from hamilton_core.session import protocol as P
from hamilton_core.session.claude_sdk_adapter import (
    FOREGROUND, ClaudeSdkAdapter, ClaudeSdkJudge, _as_question, _foreground_only,
    _translate,
)


def adapter(answerer=None, write_policy=None, **kw):
    return ClaudeSdkAdapter(root="/tmp",
                            answerer=answerer or (lambda q: "ok"),
                            write_policy=write_policy or (lambda p: None), **kw)


# --- session options ---------------------------------------------------------

def test_project_settings_are_loaded_so_the_hamilton_skill_applies():
    o = adapter()._options
    assert o.setting_sources == ["project"]
    assert o.skills == ["hamilton"]
    assert "hamilton" in o.mcp_servers


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
    assert r.behavior == "deny" and "hamilton review" in r.message


# --- the reviewer ------------------------------------------------------------

def test_the_judge_session_has_no_tools_settings_or_history():
    o = ClaudeSdkJudge()._options("/tmp/empty")
    assert o.tools == [] and o.allowed_tools == []
    assert o.setting_sources == [] and o.strict_mcp_config is True
    assert o.mcp_servers == {} and o.resume is None
    assert o.max_turns == 1 and o.cwd == "/tmp/empty"


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


def progress(tool_use_id, tool_uses, last_tool):
    return TaskProgressMessage(
        "task_progress", {}, task_id="t1", description="running tests",
        usage={"total_tokens": 1, "tool_uses": tool_uses, "duration_ms": 1},
        uuid="u", session_id="s", tool_use_id=tool_use_id, last_tool_name=last_tool)


def test_the_main_agents_text_is_shown():
    assert _translate(said("Tests are green.")) == [P.AgentText("Tests are green.")]


def test_a_subagents_text_and_calls_are_not():
    assert _translate(said("thinking aloud", parent="X")) == []
    assert _translate(called("Bash", "b1", parent="X", command="ls")) == []
    assert _translate(returned("b1", parent="X")) == []
    assert _translate(UserMessage("the subagent's prompt", parent_tool_use_id="X")) == []


def test_a_subagent_is_a_task_from_start_to_end():
    assert _translate(called("Agent", "X", description="Write test R-0001/AC1",
                             prompt="...")) == [P.TaskStarted("X", "Write test R-0001/AC1")]
    assert _translate(progress("X", 3, "Edit")) == [P.TaskProgress("X", 3, "Edit")]
    assert _translate(returned("X")) == [P.TaskEnded("X", True)]
    assert _translate(returned("X", is_error=True)) == [P.TaskEnded("X", False)]


def test_any_other_tool_is_no_task_and_its_end_is_left_to_the_agent_to_ignore():
    assert _translate(called("Bash", "b2", command="hamilton check")) == []
    assert _translate(returned("b2")) == [P.TaskEnded("b2", True)]


def test_task_frames_without_a_tool_call_are_ignored():
    started = TaskStartedMessage("task_started", {}, task_id="t2",
                                 description="npm test", uuid="u", session_id="s",
                                 tool_use_id="b3")
    assert _translate(started) == []


def test_only_our_own_turns_result_ends_the_turn():
    assert _translate(result()) == [P.TurnEnded()]
    assert _translate(result(origin={"kind": "human"})) == [P.TurnEnded()]
    assert _translate(result(origin={"kind": "task-notification"})) == []


def test_a_failed_result_is_a_session_error():
    assert _translate(result(is_error=True, text="out of budget")) == [
        P.SessionError("out of budget"), P.TurnEnded()]


def test_the_foreground_hook_is_installed_for_the_subagent_tool():
    [matcher] = adapter()._options.hooks["PreToolUse"]
    assert matcher.matcher == "Agent|Task" and matcher.hooks == [_foreground_only]


def test_a_background_subagent_is_refused_and_a_foreground_one_allowed():
    def hook(**tool_input):
        return asyncio.run(_foreground_only(
            {"tool_name": "Agent", "tool_input": tool_input}, "X", None))

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
