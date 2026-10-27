"""The reviewer checks that need more than the pack: capture, policy coverage, news vs flags, merge fidelity, notes."""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from kyc_common import KycPack  # noqa: E402

from app.agents.review_capture import RunCapture, parse_json_object  # noqa: E402
from app.agents.review_checks import (SPECIALISTS, coverage, flag_producers, log_notes, merge_findings,  # noqa: E402
                                      news_flag_findings, reply_schema_findings)
from app.agents.reviewer import render_summary, review_output, send_back_message  # noqa: E402

PROMPTS = ROOT / "services" / "api" / "app" / "prompts"
EXAMPLE = json.loads((PROMPTS / "example_pack.json").read_text())


def _raw(**changes) -> dict:
    pack = copy.deepcopy(EXAMPLE)
    pack.update(changes)
    return pack


def _pack(**changes) -> KycPack:
    return KycPack.model_validate(_raw(**changes))


def _sections() -> dict[str, str]:
    text = (ROOT / "data" / "documents" / "policies" / "kyc_policy.md").read_text()
    return {"kyc_policy.md#8": "## 8." + text.split("## 8.", 1)[1].split("\n## 9.", 1)[0].strip()}


ADVERSE = {"title": "Supplier sues Example Ltd", "url": "https://news.example/sues", "sentiment": "adverse",
           "severity": "medium", "sources": ["src-3"]}
ADVERSE_FLAG = {"type": "adverse_media", "severity": "medium", "message": "A supplier has sued the company.",
                "sources": ["src-3"]}


# --- capture ----------------------------------------------------------------------------------------------------

def test_parse_json_object_accepts_bare_fenced_and_prose_wrapped_json():
    assert parse_json_object('{"a": 1}') == {"a": 1}
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('Here is the result: {"a": {"b": 2}} done.') == {"a": {"b": 2}}
    assert parse_json_object("[1, 2]") is None and parse_json_object("no json here") is None


def test_capture_keeps_the_latest_reply_and_counts_runs():
    capture = RunCapture()
    capture.record_subagent("news", '{"news": []}')
    capture.record_subagent("news", '{"news": [1]}')
    capture.record_subagent("bank", "not json")
    assert capture.subagents["news"] == {"news": [1]} and capture.runs["news"] == 2
    assert "bank" in capture.unparsed and "bank" not in capture.subagents


# --- coverage ---------------------------------------------------------------------------------------------------

def test_example_pack_has_nothing_missing():
    assert coverage(_pack(), None, False) == []


def test_null_identity_field_needs_a_missing_data_flag():
    raw = _raw()
    raw["company"]["address"] = {"value": None, "sources": []}
    items = coverage(KycPack.model_validate(raw), None, False)
    assert [(i.path, i.status) for i in items] == [("company.address", "null_without_flag")]
    raw["flags"].append({"type": "missing_data", "severity": "low", "message": "No address.", "sources": ["src-1"]})
    assert coverage(KycPack.model_validate(raw), None, False) == []


def test_inactive_status_needs_an_inactive_company_flag_and_active_must_not_have_one():
    raw = _raw()
    raw["company"]["status"] = {"value": "dissolved", "sources": ["src-1"]}
    item = coverage(KycPack.model_validate(raw), None, False)[0]
    assert item.path == "flags" and item.status == "absent" and item.owner == "evaluator"
    raw = _raw()
    raw["flags"].append({"type": "inactive_company", "severity": "high", "message": "Inactive.", "sources": ["src-1"]})
    assert [i.status for i in coverage(KycPack.model_validate(raw), None, False)] == ["unsupported"]
    raw["company"]["status"] = {"value": "ATIVA", "sources": ["src-1"]}
    assert [i.status for i in coverage(KycPack.model_validate(raw), None, False)] == ["unsupported"]


def test_corporate_owner_needs_complex_ownership_and_the_flag_needs_a_corporate_owner():
    raw = _raw()
    raw["flags"] = [f for f in raw["flags"] if f["type"] != "complex_ownership"]
    item = coverage(KycPack.model_validate(raw), None, False)[0]
    assert item.path == "flags" and item.owner == "ownership" and item.status == "absent"
    raw = _raw()
    raw["owners"] = [o for o in raw["owners"] if o["type"] == "person"]
    for o in raw["owners"]:
        o["via"] = []
    assert [i.status for i in coverage(KycPack.model_validate(raw), None, False)] == ["unsupported"]


def test_missing_financials_need_a_missing_data_flag():
    item = coverage(_pack(financial_summary=None), None, False)[0]
    assert item.path == "financial_summary" and item.owner == "financials"


def test_every_specialist_must_have_run_on_a_fresh_run_only():
    capture = RunCapture(runs={name: 1 for name in SPECIALISTS if name != "news"})
    items = coverage(_pack(), capture, expect_all_specialists=True)
    assert [(i.path, i.owner, i.status) for i in items] == [("news", "news", "absent")]
    assert coverage(_pack(), capture, expect_all_specialists=False) == []


# --- news flags against news sources -------------------------------------------------------------------------------

def test_consistent_news_and_flag_pass():
    raw = _raw(news=[ADVERSE])
    raw["flags"].append(ADVERSE_FLAG)
    assert news_flag_findings(KycPack.model_validate(raw)) == []


def test_adverse_article_without_a_flag_is_blocking():
    problems = news_flag_findings(_pack(news=[ADVERSE]))
    assert [(p.path, p.category, p.blocking) for p in problems] == [("news[0]", "flags", True)]


def test_adverse_flag_without_an_adverse_article_is_unsupported():
    raw = _raw()
    raw["flags"].append(ADVERSE_FLAG)
    problems = news_flag_findings(KycPack.model_validate(raw))
    assert problems[0].path == "flags[2]" and problems[0].cause == "unsupported_claim"


def test_flag_severity_must_match_the_article_severity():
    raw = _raw(news=[{**ADVERSE, "severity": "high"}])
    raw["flags"].append(ADVERSE_FLAG)
    problems = news_flag_findings(KycPack.model_validate(raw))
    assert [p.path for p in problems] == ["flags[2]"] and "'high'" in problems[0].message


def test_decline_for_adverse_media_needs_a_high_severity_article():
    raw = _raw(news=[ADVERSE])
    raw["flags"].append(ADVERSE_FLAG)
    raw["risk"]["recommendation"] = "decline"
    problems = news_flag_findings(KycPack.model_validate(raw))
    assert [p.path for p in problems] == ["risk.recommendation"]


# --- merge fidelity -----------------------------------------------------------------------------------------------

def test_bank_conflict_flag_must_survive_the_merge_unchanged():
    conflict = {"type": "conflicting_data", "message": "Address differs between the register and the bank."}
    capture = RunCapture(subagents={"bank": {"flags": [conflict]}})
    pack = _pack()
    problems = merge_findings(pack, capture)
    assert [p.path for p in problems] == ["flags"]
    assert problems[0].expected == conflict["message"] and EXAMPLE["flags"][1]["message"][:40] in problems[0].actual
    pack.flags[1].message = "  address DIFFERS between the register   and the bank. "
    assert merge_findings(pack, capture) == []


def test_officers_and_owners_must_be_taken_as_returned():
    capture = RunCapture(subagents={"ownership": {
        "officers": [{"name": "Ada Quillfeather"}],
        "owners": [{"name": "Harbour Holdings Limited", "type": "company", "is_ultimate_beneficial_owner": False},
                   {"name": "Ada Quillfeather", "type": "person", "is_ultimate_beneficial_owner": True},
                   {"name": "Jonas Brandt", "type": "person", "is_ultimate_beneficial_owner": True}]}})
    problems = merge_findings(_pack(), capture)
    assert [p.path for p in problems] == ["officers"]
    assert "Jonas Brandt".casefold() in problems[0].message.casefold()
    assert problems[0].produced_by == "orchestrator" and problems[0].cause == "merge_changed"


def test_financials_and_risk_must_match_the_specialist_output():
    capture = RunCapture(subagents={
        "financials": {"financial_summary": {"year": 2025, "revenue": 9_999_999, "net_profit": 318000}},
        "evaluator": {"risk": {"rating": "high", "recommendation": "decline"}}})
    paths = sorted(p.path for p in merge_findings(_pack(), capture))
    assert paths == sorted(["financial_summary", "risk.rating", "risk.recommendation"])


def test_evaluators_policy_excerpt_must_not_be_retyped():
    capture = RunCapture(subagents={"evaluator": {"sources": [
        {"id": "src-6", "reference": "kyc_policy.md#8", "excerpt": "Conflicting data | 1 points exactly"}]}})
    problems = merge_findings(_pack(), capture)
    assert [p.path for p in problems] == ["sources[src-6].excerpt"] and problems[0].produced_by == "orchestrator"


def test_flags_are_attributed_to_the_specialist_that_returned_them():
    capture = RunCapture(subagents={"ownership": {"flags": [
        {"type": "complex_ownership", "message": pack_flag_message()}]}})
    owners = flag_producers(_pack(), capture)
    assert owners == {"flags[0]": "ownership", "flags[1]": "orchestrator"}


def pack_flag_message() -> str:
    return EXAMPLE["flags"][0]["message"]


# --- through the reviewer ---------------------------------------------------------------------------------------

def test_missing_items_fail_the_review_and_show_up_everywhere():
    raw = _raw(financial_summary=None)
    feedback, pack = review_output(raw, sections=_sections(), round_no=1, max_sendbacks=1)
    assert pack is None and feedback.verdict == "fail" and not feedback.blocking
    assert [(m.path, m.suggested_owner) for m in feedback.missing] == [("financial_summary", "financials")]
    assert next(s for s in feedback.fields if s.field == "financial_summary").status == "missing"
    assert "Missing: financial_summary" in render_summary(feedback)
    payload = json.loads(send_back_message(feedback).split("<review_feedback>")[1].split("</review_feedback>")[0])
    assert payload["missing"][0]["path"] == "financial_summary"


def test_flag_findings_name_the_specialist_that_raised_the_flag():
    raw = _raw()
    raw["flags"].append(ADVERSE_FLAG)  # an adverse_media flag with no adverse article
    capture = RunCapture(subagents={"news": {"flags": [ADVERSE_FLAG]}}, runs={name: 1 for name in SPECIALISTS})
    feedback, _ = review_output(raw, sections=_sections(), round_no=1, max_sendbacks=1, capture=capture)
    finding = next(f for f in feedback.blocking if f.path == "flags[2]")
    assert finding.produced_by == "news" and finding.suggested_owner == "news" and finding.category == "flags"


def test_tool_errors_and_unparsed_replies_become_suggestions():
    capture = RunCapture(unparsed={"bank": "not json"})
    call = capture.record_call("news", "news_search", {"company_name": "Example Ltd"})
    call.is_error = True
    notes = log_notes(capture)
    assert {n.about for n in notes} == {"tool error: news_search", "bank reply"}
    feedback, _ = review_output(_raw(), sections=_sections(), round_no=1, max_sendbacks=1, capture=capture,
                                expect_all_specialists=False)
    assert feedback.verdict == "pass" and len(feedback.suggestions) == 2


# --- reworded vs missing, and values the specialist got wrong -----------------------------------------------------

def test_a_reworded_bank_conflict_flag_is_advisory_and_a_missing_one_blocks():
    conflict = {"type": "conflicting_data", "message": "Address differs.", "sources": ["src-1", "src-4"]}
    capture = RunCapture(subagents={"bank": {"flags": [conflict]}})
    problems = merge_findings(_pack(), capture)   # the example's conflicting_data flag cites the same sources
    assert [(p.path, p.blocking) for p in problems] == [("flags", False)] and "reworded" in problems[0].message
    raw = _raw()
    raw["flags"] = [f for f in raw["flags"] if f["type"] != "conflicting_data"]
    problems = merge_findings(KycPack.model_validate(raw), capture)
    assert [(p.path, p.blocking) for p in problems] == [("flags", True)] and "missing" in problems[0].message
    other = {**conflict, "sources": ["src-2"]}   # a flag for another difference: not the same one, so it blocks
    assert merge_findings(_pack(), RunCapture(subagents={"bank": {"flags": [other]}}))[0].blocking


def test_values_the_specialist_got_wrong_do_not_blame_the_orchestrator_for_translating_them():
    capture = RunCapture(subagents={"news": {"news": [
        {"url": ADVERSE["url"], "sentiment": "negative", "severity": "low"}]}})
    pack = _pack(news=[{**ADVERSE, "sentiment": "adverse", "severity": "none"}])
    assert merge_findings(pack, capture) == []
    capture.subagents["news"]["news"][0].update(sentiment="neutral", severity="none")   # valid values: compared
    problems = merge_findings(pack, capture)
    assert [p.path for p in problems] == ["news"] and "changed from" in problems[0].actual
    capture.subagents["news"]["news"][0]["url"] = "https://news.example/other"           # another article: always compared
    assert [p.path for p in merge_findings(pack, capture)] == ["news"]


def test_a_specialist_reply_with_values_outside_the_schema_is_reported_against_the_specialist():
    capture = RunCapture(subagents={"news": {"news": [
        {"title": "t", "url": "u", "sentiment": "negative", "severity": "low"}]}})
    problems = reply_schema_findings(capture)
    assert {p.path for p in problems} == {"news.news[0].sentiment", "news.news[0].severity"}
    assert all(not p.blocking and p.produced_by == "news" and p.suggested_owner == "news" for p in problems)
    extra = RunCapture(subagents={"news": {"news": [{"title": "t", "url": "u", "relevance": 1}]}})
    assert reply_schema_findings(extra) == []   # extra keys are ignored


def test_reply_schema_problems_reach_the_feedback_as_advisory_findings():
    capture = RunCapture(subagents={"news": {"news": [], "flags": [{"type": "negative_press", "message": "m"}]}},
                         runs={name: 1 for name in SPECIALISTS})
    feedback, pack = review_output(_raw(), sections=_sections(), round_no=1, max_sendbacks=1, capture=capture)
    finding = next(f for f in feedback.advisory if f.path == "news.flags[0].type")
    assert finding.produced_by == "news" and finding.category == "schema" and feedback.verdict == "pass"


def test_an_adverse_flag_and_article_that_cite_different_sources_are_only_a_warning():
    raw = _raw(news=[ADVERSE])                                       # the article cites src-3
    raw["flags"].append({**ADVERSE_FLAG, "sources": ["src-1"]})      # the flag cites src-1
    problems = news_flag_findings(KycPack.model_validate(raw))
    assert sorted(p.path for p in problems) == ["flags[2]", "news[0]"] and not any(p.blocking for p in problems)
    assert "src-1" in problems[0].actual + problems[1].actual and "src-3" in problems[0].expected + problems[1].expected
    raw["flags"] = [f for f in raw["flags"] if f["type"] != "adverse_media"]   # no flag at all: blocks
    assert [(p.path, p.blocking) for p in news_flag_findings(KycPack.model_validate(raw))] == [("news[0]", True)]
