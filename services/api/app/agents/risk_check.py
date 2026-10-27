"""Deterministic check of `risk` against KYC policy sections 8 and 9, so the model's arithmetic is never trusted blindly."""
from __future__ import annotations

import re

from kyc_common import KycPack

POLICY_PREFIX = "kyc_policy.md#"


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


MIN_WORD_OVERLAP = 0.8  # share of the excerpt's words that must appear in the section text


def _words(s: str) -> list[str]:
    return re.findall(r"\w+", s.lower())


def _quoted_in(excerpt: str, text: str) -> bool:
    """True when at least MIN_WORD_OVERLAP of the excerpt's words appear in the section text. Order, punctuation,
    markdown and '...' are ignored, so a trimmed or reformatted quote passes but an invented rule does not."""
    words, section = _words(excerpt), set(_words(text))
    return bool(words) and sum(w in section for w in words) / len(words) >= MIN_WORD_OVERLAP


def _policy_sources(pack: KycPack) -> list:
    """Policy `document` sources cited by at least one risk factor."""
    by_id = {s.id: s for s in pack.sources}
    ids = {sid for f in pack.risk.factors for sid in f.sources}
    return [by_id[sid] for sid in ids if sid in by_id and by_id[sid].reference.startswith(POLICY_PREFIX)]


def citation_problems(pack: KycPack, retrieved: dict[str, str]) -> list[str]:
    """The rating must cite the policy clause behind it: when factors carry points, some factor must cite section 8.
    Every cited policy source must come from a section the agent retrieved with `policy_search`, and its excerpt must
    be quoted verbatim from the returned section text."""
    if not retrieved:
        return ["risk: no policy section was retrieved. Call policy_search for the risk rating points system and "
                "the recommendation rules, and cite the sections you apply."]
    cited = _policy_sources(pack)
    problems = []
    if any(f.points for f in pack.risk.factors) and not any(s.reference == f"{POLICY_PREFIX}8" for s in cited):
        problems.append("risk: the rating comes from the points table, so cite policy section 8 (a 'document' source "
                        "whose reference is the `source` returned by policy_search, e.g. 'kyc_policy.md#8') on the "
                        "risk factors that use it.")
    for src in sorted(cited, key=lambda s: s.id):
        text = retrieved.get(src.reference)
        if text is None:
            problems.append(f"sources: '{src.id}' cites '{src.reference}', which policy_search did not return.")
        elif not src.excerpt or not _quoted_in(src.excerpt, text):
            problems.append(f"sources: '{src.id}' excerpt is not quoted verbatim from the text of "
                            f"'{src.reference}' returned by policy_search: fewer than {MIN_WORD_OVERLAP:.0%} of its "
                            f"words appear in that text. Copy the rule from that text. "
                            f"Do not call policy_search yourself if you are the orchestrator: keep the evaluator's "
                            f"excerpt unchanged or ask the evaluator for a corrected one.")
    return problems


def risk_warnings(pack: KycPack) -> list[str]:
    """Non-blocking gaps: the recommendation should cite policy section 9, but the pack stays valid without it."""
    if not any(s.reference == f"{POLICY_PREFIX}9" for s in _policy_sources(pack)):
        return ["risk: the recommendation does not cite policy section 9 on any risk factor."]
    return []


def risk_problems(pack: KycPack, retrieved: dict[str, str] | None = None) -> list[str]:
    """Violations of the policy's points table and recommendation rule that can be verified from the pack.
    When `retrieved` (policy_search results by source) is given, policy citations are enforced too."""
    risk, problems = pack.risk, []
    if retrieved is not None:
        problems += citation_problems(pack, retrieved)
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
