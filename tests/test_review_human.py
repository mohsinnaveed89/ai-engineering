"""Slice 4: the human send-back flow. The unrequested-change check, the triage feedback, and the loop with a revision
(a fake agent session and a fake triage: no LLM, no database)."""
import asyncio
import dataclasses
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from kyc_common import KycPack  # noqa: E402

from app.agents import kyc_agent  # noqa: E402
from app.agents.review_checks import ANY_SECTION, REGRESSION_RULE, SPECIALISTS, unrequested_changes  # noqa: E402
from app.agents.reviewer import PackRejected  # noqa: E402
from app.agents.reviewer_llm import LlmReview  # noqa: E402
from app.agents.reviewer_triage import (LlmTriage, TriagePoint, build_triage_message, requested_sections,  # noqa: E402
                                        triage_feedback, triage_message)
from app.context import PipelineContext  # noqa: E402
from app.settings import settings  # noqa: E402

PROMPTS = ROOT / "services" / "api" / "app" / "prompts"


def _raw() -> dict:
    return json.loads((PROMPTS / "example_pack.json").read_text())


def _pack(raw: dict | None = None) -> KycPack:
    return KycPack.model_validate(raw or _raw())


def _changed(**edits) -> dict:
    """The example pack with dotted-path edits, e.g. _changed(**{"financial_summary.revenue": 1})."""
    raw = _raw()
    for dotted, value in edits.items():
        *path, last = dotted.split(".")
        node = raw
        for key in path:
            node = node[int(key)] if key.isdigit() else node[key]
        node[int(last) if last.isdigit() else last] = value
    return raw


# --- unrequested changes -----------------------------------------------------------------------------------------------

def test_an_unchanged_pack_has_no_unrequested_changes():
    assert unrequested_changes(_pack(), _pack(), {"owners"}) == []


def test_a_change_to_a_requested_section_is_allowed():
    new = _pack(_changed(**{"financial_summary.revenue": 12345}))
    assert unrequested_changes(_pack(), new, {"financial_summary"}) == []


def test_a_change_nobody_asked_for_blocks_in_the_identity_sections():
    new = _pack(_changed(**{"company.address.value": "1 Somewhere Else Road"}))
    [problem] = unrequested_changes(_pack(), new, {"financial_summary"})
    assert problem.path == "company" and problem.blocking and problem.rule == REGRESSION_RULE
    assert problem.produced_by == "orchestrator" and "address" in problem.actual


def test_a_change_to_news_flags_or_risk_is_advisory():
    new = _pack(_changed(**{"risk.rationale": "A different rationale."}))
    [problem] = unrequested_changes(_pack(), new, {"financial_summary"})
    assert problem.path == "risk" and not problem.blocking


def test_risk_may_change_when_evidence_that_feeds_it_was_requested():
    new = _pack(_changed(**{"financial_summary.revenue": 5, "risk.rationale": "Updated for the new revenue."}))
    assert unrequested_changes(_pack(), new, {"financial_summary"}) == []
    # ...but not when the requested section does not feed the score
    new = _pack(_changed(**{"officers.0.role": "chair", "risk.rationale": "Updated."}))
    assert [p.path for p in unrequested_changes(_pack(), new, {"officers"})] == ["risk"]


def test_list_order_and_source_id_lists_are_ignored():
    raw = _raw()
    raw["officers"].reverse()
    for officer in raw["officers"]:
        officer["sources"] = list(reversed(officer["sources"])) + list(officer["sources"][:0])
    raw["sources"].reverse()
    assert unrequested_changes(_pack(), _pack(raw), set()) == []


def test_an_unmapped_request_switches_the_check_off():
    new = _pack(_changed(**{"company.address.value": "elsewhere"}))
    assert unrequested_changes(_pack(), new, {ANY_SECTION}) == []


# --- triage feedback ---------------------------------------------------------------------------------------------------

def _triage(*points: TriagePoint) -> LlmTriage:
    return LlmTriage(points=list(points), summary="Two points.")


def test_triage_maps_each_point_and_path_to_a_human_finding():
    feedback = triage_feedback("check the UBOs and the 2025 revenue", _triage(
        TriagePoint(comment="check the UBOs", request="Re-check the owners.", paths=["owners", "officers"],
                    suggested_owner="ownership"),
        TriagePoint(comment="the 2025 revenue", request="Use the 2025 revenue.", paths=["financial_summary.revenue"],
                    suggested_owner="financials", raised_before=True)), max_sendbacks=1)
    assert feedback.trigger == "human_feedback" and feedback.verdict == "revise"
    assert [(f.path, f.suggested_owner, f.produced_by) for f in feedback.findings] == [
        ("owners", "ownership", "ownership"), ("officers", "ownership", "ownership"),
        ("financial_summary.revenue", "financials", "financials")]
    assert all(f.origin == "human" and f.category == "human_feedback" and f.severity == "blocking"
               for f in feedback.findings)
    assert feedback.findings[0].reviewer_comment == "check the UBOs" and feedback.findings[2].still_unresolved
    assert requested_sections(feedback) == {"owners", "officers", "financial_summary"}


def test_unsupported_and_ambiguous_points_are_advisory_but_only_unsupported_requests_nothing():
    feedback = triage_feedback("make it low risk", _triage(
        TriagePoint(comment="make it low risk", request="Lower the rating.", paths=["risk.rating"],
                    status="unsupported", note="the rating follows the policy points"),
        TriagePoint(comment="the UBO", request="Check the UBO.", paths=["owners"], status="ambiguous",
                    note="no holding company is shown")), 1)
    unsupported, ambiguous = feedback.findings
    assert unsupported.severity == "advisory" and unsupported.note.startswith("unsupported: the rating follows")
    assert ambiguous.severity == "advisory" and ambiguous.note.startswith("ambiguous:")
    assert requested_sections(feedback) == {"owners"}   # the agent may find something when it looks


def test_only_unsupported_points_keep_the_regression_check_on_with_nothing_requested():
    feedback = triage_feedback("make it low risk", _triage(
        TriagePoint(comment="make it low risk", request="Lower the rating.", paths=["risk.rating"],
                    status="unsupported")), 1)
    assert requested_sections(feedback) == set()
    new = _pack(_changed(**{"flags.0.message": "A different message."}))
    assert [p.path for p in unrequested_changes(_pack(), new, requested_sections(feedback))] == ["flags"]


def test_a_point_without_a_path_switches_the_regression_check_off():
    feedback = triage_feedback("tidy up", _triage(TriagePoint(comment="tidy up", request="Tidy up.")), 1)
    assert requested_sections(feedback) == {ANY_SECTION}


def test_without_a_triage_the_comment_goes_through_as_written():
    feedback = triage_feedback("Check the UBOs", None, 1, error="timed out after 180s")
    [finding] = feedback.findings
    assert finding.reviewer_comment == "Check the UBOs" and finding.severity == "blocking" and not finding.path
    assert "timed out" in feedback.suggestions[0].based_on and requested_sections(feedback) == {ANY_SECTION}


def test_the_orchestrator_receives_the_triage_without_noise():
    feedback = triage_feedback("check the UBOs", _triage(
        TriagePoint(comment="check the UBOs", request="Re-check the owners.", paths=["owners"])), 1)
    message = triage_message(feedback)
    payload = json.loads(message.removeprefix("<review_feedback>").removesuffix("</review_feedback>"))
    assert payload["verdict"] == "revise" and payload["findings"][0]["path"] == "owners"
    assert "fields" not in payload and "missing" not in payload


def test_the_triage_prompt_gets_the_comment_the_pack_and_only_earlier_decisions():
    ctx = PipelineContext(run_id="t", company_name="Example Ltd", country="UK", version=3, feedback="Check the UBOs",
                          previous_pack=_pack(), review_history=[
                              {"version": 1, "decision": "sent_back", "comments": "Add the revenue"},
                              {"version": 2, "decision": "sent_back", "comments": "Check the UBOs"}])
    message = build_triage_message(ctx)
    assert "<comment>Check the UBOs</comment>" in message and "Add the revenue" in message
    assert message.count("Check the UBOs") == 1   # the current comment is not repeated as an earlier decision
    assert "<previous_pack>" in message


# --- the loop in a human revision --------------------------------------------------------------------------------------

def _result(output):
    return SimpleNamespace(structured_output=output, terminal_reason="completed", subtype="success", is_error=False,
                           num_turns=3, result="")


class _FakeClient:
    def __init__(self, options=None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def revise(monkeypatch):
    """revise(results, triage, comment) -> (outcome, orchestrator prompts, events); `triage` is the (LlmTriage | None,
    error) the fake triage returns, or None to leave the LLM off."""
    def go(results, triage=None, comment="Update the revenue", max_sendbacks=1):
        prompts, events, queue = [], [], list(results)

        async def emit(event):
            events.append(event)

        async def fake_triage(ctx, sections, team):
            return triage

        async def fake_llm_review(ctx, structured_output, capture, code_feedback, sections, team):
            return LlmReview(), None   # the review stage has nothing to add; it is not what these tests are about

        async def fake_turn(client, ctx, prompt, capture):
            prompts.append(prompt)
            capture.runs.update({name: 1 for name in SPECIALISTS})
            return queue.pop(0)

        async def fake_sections(database_url, retrieved):
            text = (ROOT / "data" / "documents" / "policies" / "kyc_policy.md").read_text()
            return {"kyc_policy.md#8": "## 8." + text.split("## 8.", 1)[1].split("\n## 9.", 1)[0].strip()}, []

        monkeypatch.setattr(kyc_agent, "ClaudeSDKClient", _FakeClient)
        monkeypatch.setattr(kyc_agent, "build_options", lambda ctx: None)
        monkeypatch.setattr(kyc_agent, "_run_turn", fake_turn)
        monkeypatch.setattr(kyc_agent, "policy_ground_truth", fake_sections)
        monkeypatch.setattr(kyc_agent, "run_llm_triage", fake_triage)
        monkeypatch.setattr(kyc_agent, "run_llm_review", fake_llm_review)
        ctx = PipelineContext(run_id="t", company_name="Example Ltd", country="UK", version=2, feedback=comment,
                              previous_pack=_pack(), _emit=emit,
                              settings=dataclasses.replace(settings, review_max_sendbacks=max_sendbacks,
                                                           review_llm_enabled=triage is not None))
        try:
            outcome = asyncio.run(kyc_agent.run_kyc_agent(ctx))
        except PackRejected as e:
            outcome = e
        return outcome, prompts, events
    return go


REVENUE_TRIAGE = (_triage(TriagePoint(comment="Update the revenue", request="Use the revised revenue.",
                                      paths=["financial_summary.revenue"], suggested_owner="financials")), None)


def test_the_first_message_of_a_revision_carries_the_triage_and_the_previous_pack(revise):
    pack, prompts, events = revise([_result(_changed(**{"financial_summary.revenue": 777}))], REVENUE_TRIAGE)
    assert pack.financial_summary.revenue == 777 and len(prompts) == 1
    assert "<feedback>Update the revenue</feedback>" in prompts[0]
    assert "<review_feedback>" in prompts[0] and "financial_summary.revenue" in prompts[0]
    assert "<previous_pack>" in prompts[0] and "change only what the comment asks for" in prompts[0]
    triage_events = [e for e in events if (e.get("data") or {}).get("feedback", {}).get("trigger") == "human_feedback"]
    assert len(triage_events) == 1 and triage_events[0]["data"]["feedback"]["verdict"] == "revise"


def test_an_unrequested_change_is_sent_back_and_passes_once_reverted(revise):
    drifted = _changed(**{"financial_summary.revenue": 777, "company.address.value": "1 Somewhere Else Road"})
    pack, prompts, _ = revise([_result(drifted), _result(_changed(**{"financial_summary.revenue": 777}))],
                              REVENUE_TRIAGE)
    assert len(prompts) == 2 and "company" in prompts[1] and "Restore it exactly" in prompts[1]
    assert pack.company.address.value == _pack().company.address.value


def test_an_unrequested_change_that_is_never_reverted_is_rejected(revise):
    drifted = _changed(**{"company.address.value": "1 Somewhere Else Road"})
    rejected, prompts, _ = revise([_result(drifted), _result(drifted)], REVENUE_TRIAGE)
    assert isinstance(rejected, PackRejected) and len(prompts) == 2
    assert rejected.feedback.blocking[0].path == "company" and rejected.feedback.blocking[0].rule == REGRESSION_RULE


def test_a_failed_triage_does_not_stop_the_revision_or_police_it(revise):
    pack, prompts, events = revise([_result(_changed(**{"company.address.value": "elsewhere"}))],
                                   (None, "timed out after 180s"))
    assert pack.company.address.value == "elsewhere" and len(prompts) == 1   # nothing could be judged unrequested
    assert "Update the revenue" in prompts[0] and "<review_feedback>" in prompts[0]
    assert any("Triage unavailable" in e.get("message", "") for e in events)


def test_a_revision_with_the_llm_off_passes_the_comment_on_as_written(revise):
    pack, prompts, _ = revise([_result(_raw())], triage=None)
    assert pack is not None and "Update the revenue" in prompts[0]


def test_what_an_earlier_round_asked_for_may_change_in_the_next(revise):
    """A send-back for a bad rating names `risk`; the revision may then change it without being called a regression."""
    bad = _changed(**{"financial_summary.revenue": 777, "risk.rating": "low"})   # 2 points is medium
    good = _changed(**{"financial_summary.revenue": 777})
    pack, prompts, _ = revise([_result(bad), _result(good)], REVENUE_TRIAGE)
    assert pack.risk.rating == "medium" and len(prompts) == 2


def test_the_revision_prompt_is_only_added_for_a_send_back():
    fresh = PipelineContext(run_id="t", company_name="Example Ltd", country="UK")
    revised = dataclasses.replace(fresh, feedback="Check the UBOs", previous_pack=_pack())
    assert "Revising after a human send-back" not in kyc_agent.build_options(fresh).system_prompt
    assert "Revising after a human send-back" in kyc_agent.build_options(revised).system_prompt


def test_the_llm_review_is_told_it_is_reviewing_a_revision():
    from app.agents.reviewer_llm import revision_note
    fresh = PipelineContext(run_id="t", company_name="Example Ltd", country="UK")
    revised = dataclasses.replace(fresh, feedback="Check the UBOs", previous_pack=_pack())
    assert revision_note(fresh) == ""
    note = revision_note(revised)
    assert "Check the UBOs" in note and "kept from the previous pack" in note and "responds to the comment" in note


def test_the_revision_prompt_keeps_the_comment_out_of_the_specialists_flags():
    prompt = kyc_agent.REVISION_PROMPT
    assert "worded as a task" in prompt and "Do not quote the human's comment" in prompt
    assert "Never put it in a flag" in prompt


def test_the_revision_prompt_takes_a_rerun_specialists_output_as_returned():
    assert "take its output for the fields it owns exactly as returned" in kyc_agent.REVISION_PROMPT


def test_the_triage_progress_lines_show_a_status_not_the_explanation(revise):
    _, _, events = revise([_result(_changed(**{"financial_summary.revenue": 777}))], (_triage(
        TriagePoint(comment="c", request="Use the revised revenue.", paths=["financial_summary.revenue"],
                    suggested_owner="financials", note="The 2025 report is in the register."),
        TriagePoint(comment="d", request="Lower the rating.", paths=["risk.rating"], status="unsupported",
                    note="the rating follows policy")), None))
    lines = [e["message"] for e in events if e.get("message", "").startswith("  ")]
    assert lines[0] == "  actionable [financials] financial_summary.revenue: Use the revised revenue."
    assert lines[1].startswith("  unsupported [orchestrator] risk.rating:")
