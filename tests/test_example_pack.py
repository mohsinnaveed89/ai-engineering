"""The example pack in the system prompt must stay a valid, fully sourced KycPack."""
import json
from pathlib import Path

from kyc_common import KycPack, sourcing_problems

from app.agents.risk_check import risk_problems, risk_warnings

PROMPTS = Path(__file__).resolve().parents[1] / "services" / "api" / "app" / "prompts"


def test_example_pack_is_valid_and_sourced():
    pack = KycPack.model_validate(json.loads((PROMPTS / "example_pack.json").read_text()))
    assert sourcing_problems(pack) == []
    assert pack.review.status == "pending"
    assert pack.risk.recommendation == "investigate_further"
    assert sum(f.points or 0 for f in pack.risk.factors) == 2  # medium: 2 to 3 points under the policy
    assert pack.financial_summary and pack.financial_summary.revenue == 4_210_000
    assert risk_problems(pack) == []


def test_risk_check_catches_policy_violations():
    pack = KycPack.model_validate(json.loads((PROMPTS / "example_pack.json").read_text()))
    pack.risk.rating = "low"
    assert any("2 points" in p for p in risk_problems(pack))
    pack.risk.rating, pack.risk.recommendation = "medium", "approve"
    assert any("cannot be 'approve'" in p for p in risk_problems(pack))
    pack.risk.recommendation = "investigate_further"
    pack.flags[0].type = "prohibited_sector"
    assert any("decline" in p for p in risk_problems(pack))


def _policy_text() -> dict[str, str]:
    text = (Path(__file__).resolve().parents[1] / "data" / "documents" / "policies" / "kyc_policy.md").read_text()
    section8 = "## 8." + text.split("## 8.", 1)[1].split("\n## 9.", 1)[0]
    return {"kyc_policy.md#8": section8.strip()}


def test_policy_citations_are_enforced():
    pack = KycPack.model_validate(json.loads((PROMPTS / "example_pack.json").read_text()))
    retrieved = _policy_text()
    assert risk_problems(pack, retrieved) == []
    assert any("no policy section was retrieved" in p for p in risk_problems(pack, {}))
    assert any("did not return" in p for p in risk_problems(pack, {"kyc_policy.md#9": "x"}))
    pack.sources[-1].excerpt = "Conflicting data | 5 points"
    assert any("verbatim" in p for p in risk_problems(pack, retrieved))
    pack.sources[-1].excerpt = "| Conflicting data | 1 |"
    pack.risk.factors[0].sources = ["src-2"]  # factor 1 still cites section 8
    assert risk_problems(pack, retrieved) == []
    pack.risk.factors[1].sources = ["src-1"]
    assert any("cite policy section 8" in p for p in risk_problems(pack, retrieved))


def test_missing_section_9_citation_is_only_a_warning():
    pack = KycPack.model_validate(json.loads((PROMPTS / "example_pack.json").read_text()))
    assert risk_problems(pack, _policy_text()) == []
    assert any("section 9" in w for w in risk_warnings(pack))
