"""The reviewer's code stage: structured findings, verdicts, attribution and the messages built from them."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.agents.reviewer import (PackRejected, producer_for, render_summary, review_output,  # noqa: E402
                                 send_back_message)

PROMPTS = ROOT / "services" / "api" / "app" / "prompts"


def _pack() -> dict:
    return json.loads((PROMPTS / "example_pack.json").read_text())


def _sections() -> dict[str, str]:
    text = (ROOT / "data" / "documents" / "policies" / "kyc_policy.md").read_text()
    section8 = "## 8." + text.split("## 8.", 1)[1].split("\n## 9.", 1)[0]
    return {"kyc_policy.md#8": section8.strip()}


def _review(output, **kw):
    kw.setdefault("sections", _sections())
    return review_output(output, round_no=1, max_sendbacks=1, **kw)


def test_example_pack_passes_with_only_the_section_9_advisory():
    feedback, pack = _review(_pack())
    assert feedback.verdict == "pass" and pack is not None and not feedback.blocking
    assert any("section 9" in f.problem for f in feedback.advisory)
    assert all(s.status == "ok" for s in feedback.fields)


def test_no_pack_is_a_fatal_schema_finding():
    feedback, pack = _review(None, no_pack_reason="terminal_reason=max_turns", fatal=True)
    assert pack is None and feedback.verdict == "fail" and feedback.fatal
    assert feedback.findings[0].category == "schema" and "max_turns" in feedback.findings[0].problem


def test_schema_errors_carry_their_path_and_skip_the_other_checks():
    broken = _pack()
    del broken["risk"]["rating"]
    feedback, pack = _review(broken)
    assert pack is None and feedback.verdict == "fail"
    assert [f.path for f in feedback.blocking] == ["risk.rating"]
    assert feedback.blocking[0].category == "schema" and not feedback.fatal


def test_sourcing_problem_is_attributed_to_the_field_owner():
    bad = _pack()
    bad["officers"][0]["sources"] = ["nope"]
    feedback, _ = _review(bad)
    finding = next(f for f in feedback.blocking if f.category == "sourcing")
    assert finding.path == "officers[0]" and finding.produced_by == "ownership"
    assert finding.suggested_owner == "ownership" and finding.cause == "unsupported_claim"


def test_invented_policy_excerpt_blocks_and_names_the_source():
    bad = _pack()
    bad["sources"][-1]["excerpt"] = "Conflicting data scores five points and always means decline"
    feedback, pack = _review(bad)
    finding = next(f for f in feedback.blocking if f.category == "policy")
    assert pack is None and finding.path == f"sources[{bad['sources'][-1]['id']}].excerpt"
    assert "verbatim" in finding.problem and finding.origin == "code"
    assert next(s for s in feedback.fields if s.field == "sources").status == "invalid"


def test_loose_quote_is_advisory_not_blocking():
    loose = _pack()
    loose["sources"][-1]["excerpt"] = "| Conflicting data | 1 | ... Rating: 0 to 1 = low"
    feedback, pack = _review(loose)
    assert feedback.verdict == "pass" and pack is not None
    assert any("not an exact quote" in f.problem for f in feedback.advisory)


def test_rating_arithmetic_is_checked():
    wrong = _pack()
    wrong["risk"]["rating"] = "low"
    feedback, _ = _review(wrong)
    finding = next(f for f in feedback.blocking if f.path == "risk.rating")
    assert finding.produced_by == "evaluator" and "2 points" in finding.problem
    assert finding.expected == "medium" and finding.actual == "low"


def test_missing_policy_text_degrades_to_a_blocking_finding_not_a_crash():
    feedback, _ = _review(_pack(), sections={})
    assert any("no policy section was retrieved" in f.problem for f in feedback.blocking)


def test_producer_for_paths():
    assert producer_for("sources[eval-policy-8].excerpt") == "evaluator"
    assert producer_for("sources[news-1].reference") == "news"
    assert producer_for("owners[2]") == "ownership"
    assert producer_for("financial_summary") == "financials"
    assert producer_for("flags[0]") == "unknown"
    assert producer_for("sources[src-1]") == "unknown"


def test_send_back_message_and_summary_carry_the_findings():
    bad = _pack()
    bad["risk"]["rating"] = "low"
    feedback, _ = _review(bad)
    message = send_back_message(feedback)
    assert "<review_feedback>" in message and "risk.rating" in message and "Return the full corrected pack" in message
    payload = json.loads(message.split("<review_feedback>")[1].split("</review_feedback>")[0])
    assert payload["verdict"] == "fail" and payload["findings"][0]["path"] == "risk.rating"
    summary = render_summary(feedback)
    assert "[evaluator] risk.rating" in summary and "Fields not generated correctly: risk" in summary


def test_pack_rejected_prints_the_summary():
    feedback, _ = _review(None, no_pack_reason="no result", fatal=True)
    assert str(PackRejected(feedback)) == render_summary(feedback)
