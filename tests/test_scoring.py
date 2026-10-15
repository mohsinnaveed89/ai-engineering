"""Sanity checks: a pack built from the correct answers must score 100%, and an empty pack must not."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scoring"))

from kyc_common import KycPack  # noqa: E402
from score import FIELDS, load_answers, score_pack  # noqa: E402


def oracle_pack(a: dict) -> dict:
    e = a["expected"]
    src = [{"id": "s1", "type": "registry", "title": "register", "reference": "mock://register"}]
    news = [{"title": u, "url": u, "sentiment": "adverse", "severity": "high", "sources": ["s1"]} for u in e["adverse_news_urls"]]
    fin = None
    if e["financials"]:
        fin = {**e["financials"], "summary": "From the annual report", "sources": ["s1"]}
    return {
        "query": {"company_name": a["input"]["name"], "country": a["input"]["country"]},
        "company": {
            "legal_name": {"value": e["legal_name"], "sources": ["s1"]},
            "registration_number": {"value": e["registration_number"], "sources": ["s1"]},
            "status": {"value": e["status_raw"], "sources": ["s1"]},
            "address": {"value": e["address"], "sources": ["s1"] if e["address"] else []},
        },
        "officers": [{"name": n, "role": "director", "sources": ["s1"]} for n in e["directors"]],
        "owners": [{"name": n, "type": "person", "is_ultimate_beneficial_owner": True, "sources": ["s1"]} for n in e["ultimate_beneficial_owners"]],
        "news": news,
        "financial_summary": fin,
        "risk": {"rating": e["risk_rating"], "recommendation": e["recommendation"], "rationale": "policy"},
        "flags": [{"type": f, "message": f, "sources": ["s1"]} for f in e["flags"]],
        "sources": src,
    }


def test_oracle_scores_100():
    for a in load_answers():
        pack = oracle_pack(a)
        KycPack.model_validate(pack)
        result = score_pack(a, pack)
        assert result["score"] == 1.0, (a["company_id"], {k: v for k, v in result["fields"].items() if v < 1}, result["notes"])


def test_missing_pack_scores_zero():
    a = load_answers()[0]
    assert score_pack(a, None, "no pack")["score"] == 0.0
    assert set(score_pack(a, None)["fields"]) == set(FIELDS)


def test_answers_cover_every_company():
    companies = json.loads((ROOT / "data" / "generated" / "companies.json").read_text())["companies"]
    assert {c["company_id"] for c in companies} == {a["company_id"] for a in load_answers()}
