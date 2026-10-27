"""The reviewer's LLM stage: prompt, session options, failure handling and what an LLM finding may block."""
import asyncio
import dataclasses
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.agents import reviewer_llm  # noqa: E402
from app.agents.kyc_agent import SUBAGENTS  # noqa: E402
from app.agents.review_capture import RunCapture  # noqa: E402
from app.agents.reviewer import ReviewFeedback, review_output  # noqa: E402
from app.agents.reviewer_llm import (ALLOWED_TOOLS, LlmFinding, LlmMissing, LlmReview, LlmSuggestion,  # noqa: E402
                                     _reviewer_tool_guard, apply_llm_review, build_reviewer_options,
                                     build_system_prompt, build_user_message, describe_team, run_llm_review)
from app.context import PipelineContext  # noqa: E402
from app.settings import settings  # noqa: E402

PROMPTS = ROOT / "services" / "api" / "app" / "prompts"
POLICY = (ROOT / "data" / "documents" / "policies" / "kyc_policy.md").read_text()
SECTIONS = {f"kyc_policy.md#{n}": "## " + part.split("\n## ")[0].strip()
            for n, part in ((p.split(".", 1)[0], p) for p in POLICY.split("\n## ")[1:]) if n.isdigit()}


def _output() -> dict:
    return json.loads((PROMPTS / "example_pack.json").read_text())


def _code_feedback(**kw) -> ReviewFeedback:
    feedback, _ = review_output(_output(), sections=SECTIONS, round_no=1, max_sendbacks=1, **kw)
    return feedback


def _finding(**kw) -> LlmFinding:
    base = dict(category="flags", path="flags[0]", produced_by="ownership", problem="The flag is not supported.",
                evidence=["src-2"], contradiction="No owner is a company.", blocking=True)
    return LlmFinding(**{**base, **kw})


def _ctx(**changes) -> PipelineContext:
    return PipelineContext(run_id="t", company_name="Example Ltd", country="UK",
                           settings=dataclasses.replace(settings, **changes))


# --- the prompt -------------------------------------------------------------------------------------------------

def test_team_description_comes_from_the_real_subagents():
    team = describe_team(SUBAGENTS)
    for name, prefix in (("ownership", "own-"), ("news", "news-"), ("bank", "bank-"), ("financials", "fin-"),
                         ("evaluator", "eval-")):
        line = next(line for line in team.splitlines() if line.startswith(f"- `{name}`"))
        assert f"`{prefix}`" in line and "?" not in line
    assert "policy_search" in team and "mcp__kyc__" not in team
    assert "Replies with: risk, flags, sources" in team


def test_system_prompt_has_the_team_and_the_full_policy_and_no_placeholders():
    prompt = build_system_prompt(describe_team(SUBAGENTS), SECTIONS)
    assert "{{" not in prompt and "`evaluator`" in prompt
    assert "[kyc_policy.md#8]" in prompt and "## 8. Risk rating" in prompt and "[kyc_policy.md#2]" in prompt
    assert prompt.index("[kyc_policy.md#2]") < prompt.index("[kyc_policy.md#10]")  # numeric, not alphabetical
    assert "use policy_search" in build_system_prompt("team", {})


def test_user_message_carries_the_pack_the_replies_the_calls_and_the_code_findings():
    capture = RunCapture(subagents={"news": {"news": []}})
    for i in range(70):
        capture.record_call("news", "news_search", {"i": i})
    message = build_user_message(_output(), capture, _code_feedback())
    for tag in ("pack", "specialist_replies", "tool_calls", "code_review"):
        assert f"<{tag}>" in message and f"</{tag}>" in message
    calls = json.loads(message.split("<tool_calls>")[1].split("</tool_calls>")[0])
    assert len(calls) == 60 and calls[0]["agent"] == "news"


# --- the session ------------------------------------------------------------------------------------------------

def test_reviewer_session_is_least_privilege():
    options = build_reviewer_options(_ctx(reviewer_model="a-smaller-model"), "prompt")
    assert options.tools == [] and options.allowed_tools == ALLOWED_TOOLS
    assert options.model == "a-smaller-model" and options.system_prompt == "prompt"
    assert list(options.mcp_servers) == ["kyc"] and "findings" in options.output_format["schema"]["properties"]
    guard = lambda name: asyncio.run(_reviewer_tool_guard({"tool_name": name}, None, None))  # noqa: E731
    assert guard("mcp__kyc__policy_search") == {} and guard("mcp__kyc__bank_get_risk_appetite") == {}
    for name in ("mcp__kyc__news_search", "mcp__kyc__registry_get_company", "Bash", "Read", "Agent"):
        assert guard(name)["hookSpecificOutput"]["permissionDecision"] == "deny", name


def test_the_guard_lets_the_reviewer_deliver_its_answer():
    """Regression: the guard once denied StructuredOutput, so the reviewer could never return anything."""
    assert asyncio.run(_reviewer_tool_guard({"tool_name": "StructuredOutput"}, None, None)) == {}


class _Client:
    """Stands in for ClaudeSDKClient: replays `messages`, or raises/sleeps."""
    messages: list = []
    raises: Exception | None = None
    sleep = 0.0
    options = None

    def __init__(self, options=None):
        type(self).options = options

    async def __aenter__(self):
        if self.raises:
            raise self.raises
        return self

    async def __aexit__(self, *exc):
        return False

    async def query(self, message):
        type(self).sent = message

    async def receive_response(self):
        await asyncio.sleep(self.sleep)
        for message in self.messages:
            yield message


def _result(output, subtype="success"):
    from claude_agent_sdk import ResultMessage
    return ResultMessage(subtype=subtype, duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
                         session_id="s", structured_output=output, total_cost_usd=0.01,
                         usage={"input_tokens": 5, "output_tokens": 7})


@pytest.fixture
def client(monkeypatch):
    class Fake(_Client):
        messages, raises, sleep = [], None, 0.0
    monkeypatch.setattr(reviewer_llm, "ClaudeSDKClient", Fake)
    return Fake


def _run(ctx=None):
    return asyncio.run(run_llm_review(ctx or _ctx(), _output(), RunCapture(), _code_feedback(), SECTIONS, "team"))


def test_a_good_reply_is_parsed_and_its_cost_recorded(client):
    client.messages = [_result({"findings": [], "missing": [], "suggestions": [], "summary": "Fine."})]
    review, error = _run()
    assert error is None and review.summary == "Fine." and "<pack>" in client.sent
    assert client.options.model == settings.reviewer_model


def test_cost_and_tool_calls_are_reported(client):
    from claude_agent_sdk import AssistantMessage, ToolUseBlock
    events = []

    async def emit(event):
        events.append(event)
    ctx = PipelineContext(run_id="t", company_name="X", country="UK", _emit=emit)
    client.messages = [AssistantMessage([ToolUseBlock("1", "mcp__kyc__bank_get_risk_appetite", {})], "m"),
                       _result({"findings": []})]
    review, error = asyncio.run(run_llm_review(ctx, _output(), RunCapture(), _code_feedback(), SECTIONS, "team"))
    assert error is None and review is not None
    assert any("LLM review calling bank_get_risk_appetite" == e.get("message") for e in events)
    metrics = next(e for e in events if e.get("type") == "metrics")["data"]
    assert metrics["input_tokens"] == 5 and metrics["output_tokens"] == 7


@pytest.mark.parametrize("setup,expected", [
    (lambda c: setattr(c, "raises", RuntimeError("cli died")), "RuntimeError: cli died"),
    (lambda c: setattr(c, "messages", []), "no structured output (no result)"),
    (lambda c: setattr(c, "messages", [_result(None, "error_max_structured_output_retries")]),
     "no structured output (error_max_structured_output_retries)"),
    (lambda c: setattr(c, "messages", [_result({"findings": [{"category": "nonsense"}]})]), "invalid output"),
])
def test_a_failing_llm_stage_returns_a_reason_instead_of_raising(client, setup, expected):
    setup(client)
    review, error = _run()
    assert review is None and error.startswith(expected)


def test_a_slow_llm_stage_times_out(client):
    client.sleep, client.messages = 1.0, [_result({"findings": []})]
    review, error = _run(_ctx(reviewer_timeout_s=0.01))
    assert review is None and "timed out" in error


# --- what an LLM finding may block --------------------------------------------------------------------------------

def test_a_finding_with_evidence_in_flags_or_policy_blocks():
    code = _code_feedback()   # already carries the advisory "section 9 not cited" finding
    out = apply_llm_review(code, LlmReview(findings=[_finding()]), _output())
    finding = out.findings[-1]
    assert finding.severity == "blocking" and finding.origin == "llm" and finding.id == f"F{len(code.findings) + 1}"
    assert out.verdict == "fail" and out.blocking == [finding]
    assert next(s for s in out.fields if s.field == "flags").status == "invalid"
    assert apply_llm_review(_code_feedback(), LlmReview(findings=[_finding(category="policy")]),
                            _output()).verdict == "fail"


@pytest.mark.parametrize("change", [
    dict(evidence=[]), dict(evidence=["nope"]), dict(contradiction="  "), dict(blocking=False),
    dict(category="merge"), dict(category="sourcing"),
])
def test_a_finding_without_evidence_or_in_another_category_is_only_a_warning(change):
    out = apply_llm_review(_code_feedback(), LlmReview(findings=[_finding(**change)]), _output())
    assert out.findings[-1].severity == "advisory" and out.verdict == "pass"


def test_policy_sections_count_as_evidence_even_if_the_pack_does_not_cite_them():
    out = apply_llm_review(_code_feedback(), LlmReview(findings=[_finding(evidence=["kyc_policy.md#6"])]), _output())
    assert out.verdict == "fail"


def test_a_repeat_of_a_code_finding_is_dropped():
    wrong = _output()
    wrong["risk"]["rating"] = "low"
    code, _ = review_output(wrong, sections=SECTIONS, round_no=1, max_sendbacks=1)
    dup = _finding(category="policy", path="risk.rating")
    out = apply_llm_review(code, LlmReview(findings=[dup]), wrong)
    assert len(out.findings) == len(code.findings) and all(f.origin == "code" for f in out.findings)


def test_a_missing_item_needs_evidence_or_becomes_a_suggestion():
    gap = LlmMissing(path="flags", required_by="kyc_policy.md#6", found="sector is prohibited, no flag",
                     suggested_owner="evaluator", evidence=["kyc_policy.md#6"])
    out = apply_llm_review(_code_feedback(), LlmReview(missing=[gap]), _output())
    assert out.verdict == "fail" and out.missing[0].suggested_owner == "evaluator"
    unproven = gap.model_copy(update={"evidence": []})
    out = apply_llm_review(_code_feedback(), LlmReview(missing=[unproven]), _output())
    assert out.verdict == "pass" and out.missing == [] and out.suggestions[-1].about == "possible gap: flags"


def test_llm_suggestions_are_kept_and_a_clean_review_changes_nothing():
    code = _code_feedback()
    out = apply_llm_review(code, LlmReview(suggestions=[LlmSuggestion(about="x", text="y")]), _output())
    assert out.verdict == "pass" and out.findings == code.findings and out.suggestions[-1].based_on == "LLM review"


def test_when_the_llm_stage_failed_the_code_verdict_stands_with_a_note():
    out = apply_llm_review(_code_feedback(), None, _output(), error="TimeoutError: timed out after 180s")
    assert out.verdict == "pass" and "could not be completed" in out.suggestions[-1].text
    assert out.suggestions[-1].based_on.startswith("TimeoutError")


def test_the_reviewers_own_structured_output_refusals_are_logged(client):
    from claude_agent_sdk import AssistantMessage, ToolResultBlock, ToolUseBlock, UserMessage
    events = []

    async def emit(event):
        events.append(event)
    ctx = PipelineContext(run_id="t", company_name="X", country="UK", _emit=emit)
    client.messages = [
        AssistantMessage([ToolUseBlock("s1", "StructuredOutput", {"findings": [{"category": "news"}]})], "m"),
        UserMessage([ToolResultBlock("s1", content="Output does not match required schema: /findings/0/category",
                                     is_error=True)]),
        AssistantMessage([ToolUseBlock("s2", "StructuredOutput", {"findings": []})], "m"),
        UserMessage([ToolResultBlock("s2", content="Structured output provided successfully")]),
        _result({"findings": []})]
    asyncio.run(run_llm_review(ctx, _output(), RunCapture(), _code_feedback(), SECTIONS, "team"))
    logged = [e["message"] for e in events if e.get("step") == "debug"]
    assert any("rejected: Output does not match required schema: /findings/0/category" in m for m in logged)
    assert any("accepted" in m for m in logged)


def test_the_prompt_says_other_flags_are_valid_and_what_is_not_a_defect():
    prompt = build_system_prompt(describe_team(SUBAGENTS), SECTIONS)
    assert "plus `other`" in prompt and "Never report an `other` flag as an invalid type" in prompt
    assert "no register, news or bank-record tools" in prompt


def test_with_blocking_off_every_llm_finding_is_a_warning_with_a_note_and_missing_items_become_suggestions():
    gap = LlmMissing(path="flags", required_by="kyc_policy.md#6", found="sector is prohibited, no flag",
                     evidence=["kyc_policy.md#6"])
    out = apply_llm_review(_code_feedback(), LlmReview(findings=[_finding()], missing=[gap]), _output(), can_block=False)
    finding = out.findings[-1]
    assert finding.severity == "advisory" and out.verdict == "pass" and out.missing == []
    assert finding.note == "asked to block, kept as a warning: LLM findings are advisory (REVIEW_LLM_BLOCKS=0)"
    assert out.suggestions[-1].about == "possible gap: flags" and out.suggestions[-1].based_on.endswith("advisory")


@pytest.mark.parametrize("change,reason", [
    (dict(category="merge"), "category 'merge' cannot block"),
    (dict(contradiction=" "), "no concrete contradiction given"),
    (dict(evidence=["nope"]), "evidence is not a pack source or a policy section"),
])
def test_a_downgraded_blocking_request_says_why(change, reason):
    out = apply_llm_review(_code_feedback(), LlmReview(findings=[_finding(**change)]), _output())
    assert out.findings[-1].severity == "advisory" and out.findings[-1].note.endswith(reason)


def test_a_finding_that_never_asked_to_block_has_no_note():
    out = apply_llm_review(_code_feedback(), LlmReview(findings=[_finding(blocking=False)]), _output())
    assert out.findings[-1].note == ""


def test_the_news_evaluator_and_reviewer_prompts_pin_the_values_and_the_victim_reading():
    news, evaluator = SUBAGENTS["news"].prompt, SUBAGENTS["evaluator"].prompt
    assert "never 'negative'" in news and "never 'low'" in news and "victim of a crime" in news
    assert "never re-classify an article" in evaluator
    assert "victim of a crime" in build_system_prompt("team", {})
