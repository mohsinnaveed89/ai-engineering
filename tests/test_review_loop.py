"""run_kyc_agent's review loop, driven with a fake agent session (no LLM, no database)."""
import asyncio
import dataclasses
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.agents import kyc_agent  # noqa: E402
from app.agents.review_checks import SPECIALISTS  # noqa: E402
from app.agents.reviewer import PackRejected  # noqa: E402
from app.agents.reviewer_llm import LlmFinding, LlmReview  # noqa: E402
from app.context import PipelineContext  # noqa: E402
from app.settings import settings  # noqa: E402

PROMPTS = ROOT / "services" / "api" / "app" / "prompts"


def _good() -> dict:
    return json.loads((PROMPTS / "example_pack.json").read_text())


def _bad() -> dict:
    pack = _good()
    pack["risk"]["rating"] = "low"  # 2 points is medium under policy section 8
    return pack


def _result(output, terminal_reason="completed"):
    return SimpleNamespace(structured_output=output, terminal_reason=terminal_reason, subtype="success",
                           is_error=False, num_turns=3, result="")


class _FakeClient:
    def __init__(self, options=None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def run(monkeypatch):
    """run(results, max_sendbacks, llm) -> (outcome, prompts sent to the orchestrator); outcome is a pack or
    PackRejected. `llm` is a list of (LlmReview | None, error) answers, one per review round; None leaves the LLM stage
    off. `subagents` plants specialist replies in the capture. The reviews it was asked for are in `go.llm_calls`."""
    def go(results, max_sendbacks=1, llm=None, llm_blocks=True, subagents=None):
        prompts, queue, llm_queue = [], list(results), list(llm or [])
        go.llm_calls, go.events = [], []

        async def emit(event):
            go.events.append(event)

        async def fake_llm(ctx, structured_output, capture, code_feedback, sections, team):
            go.llm_calls.append(code_feedback.round)
            return llm_queue.pop(0)

        async def fake_turn(client, ctx, prompt, capture):
            prompts.append(prompt)
            capture.runs.update({name: 1 for name in SPECIALISTS})  # every specialist ran
            capture.subagents.update(subagents or {})                # what they returned, if a test cares
            return queue.pop(0)

        async def fake_sections(database_url, retrieved):
            text = (ROOT / "data" / "documents" / "policies" / "kyc_policy.md").read_text()
            return {"kyc_policy.md#8": "## 8." + text.split("## 8.", 1)[1].split("\n## 9.", 1)[0].strip()}, []

        monkeypatch.setattr(kyc_agent, "ClaudeSDKClient", _FakeClient)
        monkeypatch.setattr(kyc_agent, "build_options", lambda ctx: None)
        monkeypatch.setattr(kyc_agent, "_run_turn", fake_turn)
        monkeypatch.setattr(kyc_agent, "policy_ground_truth", fake_sections)
        monkeypatch.setattr(kyc_agent, "run_llm_review", fake_llm)
        ctx = PipelineContext(run_id="t", company_name="Example Ltd", country="UK", _emit=emit,
                              settings=dataclasses.replace(settings, review_max_sendbacks=max_sendbacks,
                                                           review_llm_enabled=llm is not None,
                                                           review_llm_blocks=llm_blocks))
        try:
            outcome = asyncio.run(kyc_agent.run_kyc_agent(ctx))
        except PackRejected as e:
            outcome = e
        return outcome, prompts
    return go


def test_valid_pack_is_returned_without_a_send_back(run):
    pack, prompts = run([_result(_good())])
    assert pack.review.status == "pending" and pack.generated_by.startswith("agent-team-v2")
    assert len(prompts) == 1


def test_failing_pack_is_sent_back_once_with_the_feedback_then_passes(run):
    pack, prompts = run([_result(_bad()), _result(_good())])
    assert len(prompts) == 2
    assert "<review_feedback>" in prompts[1] and "risk.rating" in prompts[1]
    assert pack.risk.rating == "medium"


def test_still_failing_after_the_send_back_is_rejected_with_the_last_feedback(run):
    rejected, prompts = run([_result(_bad()), _result(_bad())])
    assert isinstance(rejected, PackRejected) and len(prompts) == 2
    assert rejected.feedback.round == 2 and rejected.feedback.blocking[0].path == "risk.rating"
    assert "Pack failed review after 2 rounds" in str(rejected)


def test_send_backs_are_configurable(run):
    rejected, prompts = run([_result(_bad())], max_sendbacks=0)
    assert isinstance(rejected, PackRejected) and len(prompts) == 1
    pack, prompts = run([_result(_bad()), _result(_bad()), _result(_good())], max_sendbacks=2)
    assert pack.risk.rating == "medium" and len(prompts) == 3


def test_running_out_of_turns_is_fatal_and_not_sent_back(run):
    rejected, prompts = run([_result(None, terminal_reason="max_turns")])
    assert isinstance(rejected, PackRejected) and rejected.feedback.fatal and len(prompts) == 1
    assert "max_turns" in rejected.feedback.findings[0].problem


# --- the LLM stage inside the loop ----------------------------------------------------------------------------------

def _blocking_llm_finding() -> LlmReview:
    return LlmReview(findings=[LlmFinding(
        category="flags", path="flags[0]", produced_by="ownership", problem="complex_ownership is not supported",
        evidence=["src-2"], contradiction="no owner is a company", blocking=True)])


def test_llm_stage_is_off_unless_enabled(run):
    pack, _ = run([_result(_good())])
    assert pack is not None and run.llm_calls == []


def test_llm_blocking_finding_is_sent_back_with_the_code_findings_then_passes(run):
    pack, prompts = run([_result(_good()), _result(_good())], llm=[(_blocking_llm_finding(), None),
                                                                      (LlmReview(), None)])
    assert len(prompts) == 2 and "flags[0]" in prompts[1] and '"origin": "llm"' in prompts[1]
    assert run.llm_calls == [1, 2] and pack is not None


def test_llm_blocking_finding_still_present_at_the_cap_rejects_the_run(run):
    rejected, prompts = run([_result(_good()), _result(_good())],
                            llm=[(_blocking_llm_finding(), None), (_blocking_llm_finding(), None)])
    assert isinstance(rejected, PackRejected) and len(prompts) == 2
    finding = rejected.feedback.blocking[0]
    assert finding.origin == "llm" and finding.produced_by == "ownership" and rejected.feedback.round == 2


def test_failed_llm_stage_does_not_fail_the_run(run):
    pack, prompts = run([_result(_good())], llm=[(None, "TimeoutError: timed out after 180s")])
    assert pack is not None and len(prompts) == 1


def test_llm_stage_runs_even_when_the_code_stage_already_failed(run):
    pack, prompts = run([_result(_bad()), _result(_good())], llm=[(LlmReview(), None), (LlmReview(), None)])
    assert run.llm_calls == [1, 2] and len(prompts) == 2 and pack is not None


def test_llm_stage_is_skipped_when_there_is_no_pack_or_the_schema_is_invalid(run):
    rejected, _ = run([_result(None, terminal_reason="max_turns")], llm=[])
    assert isinstance(rejected, PackRejected) and run.llm_calls == []
    broken = _good()
    del broken["risk"]["rating"]
    rejected, _ = run([_result(broken), _result(broken)], llm=[])
    assert isinstance(rejected, PackRejected) and run.llm_calls == []


def test_risk_line_shows_the_rating_and_the_factors_behind_it():
    line = kyc_agent._risk_line(_good())
    assert line.startswith("Pack risk: medium / investigate_further") and "Conflicting data=1" in line
    assert kyc_agent._risk_line(None) == "" and kyc_agent._risk_line({"risk": "x"}) == ""


def test_every_round_is_logged_with_its_feedback_and_a_failed_round_shows_the_whole_review(run):
    run([_result(_bad()), _result(_good())])
    rounds = [e["data"]["feedback"] for e in run.events if (e.get("data") or {}).get("feedback")]
    assert [r["round"] for r in rounds] == [1, 2] and [r["verdict"] for r in rounds] == ["fail", "pass"]
    lines = [e["message"] for e in run.events if e.get("step") == "reviewer"]
    assert any(line.startswith("  blocking code [evaluator] risk.rating") for line in lines)
    assert any(line.startswith("  advisory") and "section 9" in line for line in lines)


def test_by_default_llm_findings_only_warn_and_never_send_the_pack_back(run):
    pack, prompts = run([_result(_good())], llm=[(_blocking_llm_finding(), None)], llm_blocks=False)
    assert pack is not None and len(prompts) == 1
    warnings = [e["message"] for e in run.events if e.get("message", "").startswith("Warning:")]
    assert any("complex_ownership is not supported" in w and "REVIEW_LLM_BLOCKS=0" in w for w in warnings)


def test_a_code_finding_still_blocks_when_llm_findings_are_advisory(run):
    pack, prompts = run([_result(_bad()), _result(_good())],
                        llm=[(_blocking_llm_finding(), None), (LlmReview(), None)], llm_blocks=False)
    assert len(prompts) == 2 and pack is not None
    sent = json.loads(prompts[1].split("<review_feedback>")[1].split("</review_feedback>")[0])["findings"]
    blocking = [f for f in sent if f["severity"] == "blocking"]
    assert "risk.rating" in {f["path"] for f in blocking} and {f["origin"] for f in blocking} == {"code"}
    assert [f["severity"] for f in sent if f["origin"] == "llm"] == ["advisory"]          # the LLM one only informs


def test_the_llm_stage_still_runs_when_a_specialist_reply_had_schema_problems(run):
    """Regression: advisory reply-schema findings share the 'schema' category and once switched the LLM stage off."""
    bad_reply = {"bank": {"flags": [{"type": "other", "severity": "info", "message": "m"}]}}   # 'info' is not a severity
    pack, _ = run([_result(_good())], llm=[(LlmReview(), None)], subagents=bad_reply)
    assert pack is not None and run.llm_calls == [1]
