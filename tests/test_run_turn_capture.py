"""_run_turn fills the RunCapture from the SDK's message stream: what each subagent returned and every tool call."""
import asyncio
import json
import sys
from pathlib import Path

from claude_agent_sdk import AssistantMessage, ResultMessage, ToolResultBlock, ToolUseBlock, UserMessage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.agents import kyc_agent  # noqa: E402
from app.agents.review_capture import RunCapture  # noqa: E402
from app.context import PipelineContext  # noqa: E402


def _assistant(*blocks, parent=None):
    return AssistantMessage(content=list(blocks), model="m", parent_tool_use_id=parent)


def _user(*blocks, parent=None):
    return UserMessage(content=list(blocks), parent_tool_use_id=parent)


STREAM = [
    _assistant(ToolUseBlock("a1", "Agent", {"subagent_type": "news"}),
               ToolUseBlock("a2", "Agent", {"subagent_type": "evaluator"})),
    _assistant(ToolUseBlock("t1", "mcp__kyc__news_search", {"company_name": "Example Ltd"}), parent="a1"),
    _user(ToolResultBlock("t1", content="[1, 2, 3]", is_error=False), parent="a1"),
    _assistant(ToolUseBlock("t2", "mcp__kyc__news_search_web", {"company_name": "Example Ltd"}), parent="a1"),
    _user(ToolResultBlock("t2", content=[{"type": "text", "text": "boom"}], is_error=True), parent="a1"),
    _assistant(ToolUseBlock("p1", "mcp__kyc__policy_search", {"query": "risk rating"}), parent="a2"),
    _user(ToolResultBlock("p1", content=json.dumps({"source": "kyc_policy.md#8", "text": "## 8. Risk rating"})),
          parent="a2"),
    _user(ToolResultBlock("a1", content=[{"type": "text", "text": '```json\n{"news": [], "flags": []}\n```'}])),
    _user(ToolResultBlock("a2", content="I could not finish the evaluation.")),
    _assistant(ToolUseBlock("s1", "StructuredOutput", {"x": 1})),
    _user(ToolResultBlock("s1", content="schema error: x is not allowed", is_error=True)),
    ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=2, session_id="s",
                  structured_output={"ok": True}, total_cost_usd=0.1, usage={"input_tokens": 1, "output_tokens": 2}),
]


class _Client:
    async def query(self, prompt):
        pass

    async def receive_response(self):
        for message in STREAM:
            yield message


def test_run_turn_captures_subagent_replies_tool_calls_and_policy_text():
    events = []

    async def emit(event):
        events.append(event)

    ctx = PipelineContext(run_id="t", company_name="Example Ltd", country="UK", _emit=emit)
    capture = RunCapture()
    result = asyncio.run(kyc_agent._run_turn(_Client(), ctx, "go", capture))

    assert result.structured_output == {"ok": True}
    assert capture.subagents == {"news": {"news": [], "flags": []}} and capture.runs == {"news": 1, "evaluator": 1}
    assert "evaluator" in capture.unparsed and "news" not in capture.unparsed
    assert capture.retrieved == {"kyc_policy.md#8": "## 8. Risk rating"}

    calls = {c.tool: c for c in capture.tool_calls}
    assert set(calls) == {"news_search", "news_search_web", "policy_search"}  # Agent and StructuredOutput are not tools
    assert calls["news_search"].agent == "news" and not calls["news_search"].is_error
    assert calls["news_search"].result_chars == len("[1, 2, 3]")
    assert calls["news_search_web"].is_error and calls["news_search_web"].result_chars == len("boom")
    assert calls["policy_search"].agent == "evaluator"

    messages = [e["message"] for e in events if e.get("type") == "progress"]
    assert any(m.startswith("StructuredOutput") and "rejected: schema error" in m for m in messages)
    assert not any(m.startswith("policy_search by") for m in messages)   # the section is recorded, not logged
