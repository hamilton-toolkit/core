"""The Claude Agent SDK adapter -- the one module that imports the SDK.

Nothing here connects to a model: what is worth pinning is the translation in
both directions -- SDK tool arguments into a Hamilton `Question`, and
Hamilton's phase policy into an SDK permission result.
"""

import asyncio

from hamilton_core.session import protocol as P
from hamilton_core.session.claude_sdk_adapter import (
    ClaudeSdkAdapter, _as_question, _strip_sentinel,
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


# --- the completion sentinel -------------------------------------------------

def test_the_sentinel_is_detected_and_hidden_from_the_engineer():
    body, hit = _strip_sentinel(f"Spec is ratified.\n{P.SENTINEL}")
    assert hit is True and body.strip() == "Spec is ratified."
    assert P.SENTINEL not in body


def test_text_without_the_sentinel_is_untouched():
    assert _strip_sentinel("plain text") == ("plain text", False)
