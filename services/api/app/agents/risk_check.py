"""Deterministic check of `risk` against KYC policy sections 8 and 9, so the model's arithmetic is never trusted blindly."""
from __future__ import annotations

from kyc_common import KycPack


def expected_rating(points: int) -> str:
    return "low" if points <= 1 else "medium" if points <= 3 else "high"


def ubo_problems(pack: KycPack) -> list[str]:
    """UBOs must be natural persons; a pack with owners but no UBO needs a ubo_unidentified flag (policy section 4)."""
    problems = [f"owners: '{o.name}' is marked as a UBO but is a company; UBOs are natural persons."
                for o in pack.owners if o.is_ultimate_beneficial_owner and o.type != "person"]
    if (pack.owners and not any(o.is_ultimate_beneficial_owner for o in pack.owners)
            and "ubo_unidentified" not in {f.type for f in pack.flags}):
        problems.append("owners: no owner is marked is_ultimate_beneficial_owner and there is no ubo_unidentified "
                        "flag. Mark each natural person with 25% or more effective ownership, or raise the flag.")
    return problems


def risk_problems(pack: KycPack) -> list[str]:
    """Violations of the policy's points table and recommendation rule that can be verified from the pack."""
    risk, problems = pack.risk, []
    points = sum(f.points or 0 for f in risk.factors)
    want = expected_rating(points)
    if risk.rating != want:
        problems.append(f"risk: the factors add up to {points} points, which is '{want}' under policy section 8, "
                        f"but the rating is '{risk.rating}'.")
    flags = {f.type for f in pack.flags}
    if flags & {"inactive_company", "prohibited_sector"}:
        if risk.recommendation != "decline":
            problems.append("risk: an inactive company or prohibited sector means 'decline' under policy section 9.")
    elif risk.recommendation == "decline":
        # Only a high-severity sanctions article justifies decline otherwise; the pack must show an adverse_media flag.
        if "adverse_media" not in flags:
            problems.append("risk: 'decline' needs an inactive company, a prohibited sector or sanctions-related "
                            "adverse media, and none is flagged.")
    elif risk.rating == "low" and risk.recommendation != "approve":
        problems.append("risk: a low rating with no decline condition is 'approve' under policy section 9.")
    elif risk.rating != "low" and risk.recommendation == "approve":
        problems.append(f"risk: a '{risk.rating}' rating cannot be 'approve' under policy section 9.")
    return problems
