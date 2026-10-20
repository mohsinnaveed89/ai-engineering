"""The example pack in the system prompt must stay a valid, fully sourced KycPack."""
import json
from pathlib import Path

from kyc_common import KycPack, sourcing_problems

from app.agents.risk_check import risk_problems

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
