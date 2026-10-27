"""Deterministic check of `risk` against KYC policy sections 8 and 9, so the model's arithmetic is never trusted blindly.

Each check returns `Problem`s (what is wrong, where, which rule, blocking or not) for the reviewer. The `*_problems`
functions are thin wrappers that return only the blocking messages as plain strings."""
from __future__ import annotations

import re
from dataclasses import dataclass

from kyc_common import KycPack

POLICY_PREFIX = "kyc_policy.md#"
MIN_WORD_OVERLAP = 0.8  # share of an excerpt's words that must appear in the section text it cites


@dataclass(frozen=True)
class Problem:
    message: str
    category: str              # schema | sourcing | flags | policy | merge
    path: str = ""             # where in the pack, e.g. "risk.rating" or "sources[eval-policy-8].excerpt"
    rule: str = ""             # the pack rule or policy section it breaks
    expected: str = ""
    actual: str = ""
    cause: str = "model_error"
    blocking: bool = True
    produced_by: str = ""      # overrides the guess made from `path`
    suggested_owner: str = ""  # overrides "whoever produced it"


def _blocking(problems: list[Problem]) -> list[str]:
    return [p.message for p in problems if p.blocking]


def expected_rating(points: int) -> str:
    return "low" if points <= 1 else "medium" if points <= 3 else "high"


def ubo_findings(pack: KycPack) -> list[Problem]:
    """UBOs must be natural persons; a pack with owners but no UBO needs a ubo_unidentified flag (policy section 4)."""
    problems = [Problem(f"owners: '{o.name}' is marked as a UBO but is a company; UBOs are natural persons.",
                        "policy", f"owners[{i}]", f"{POLICY_PREFIX}4 UBOs are natural persons",
                        "type person", f"type {o.type}")
                for i, o in enumerate(pack.owners) if o.is_ultimate_beneficial_owner and o.type != "person"]
    if (pack.owners and not any(o.is_ultimate_beneficial_owner for o in pack.owners)
            and "ubo_unidentified" not in {f.type for f in pack.flags}):
        problems.append(Problem(
            "owners: no owner is marked is_ultimate_beneficial_owner and there is no ubo_unidentified "
            "flag. Mark each natural person with 25% or more effective ownership, or raise the flag.",
            "policy", "owners", f"{POLICY_PREFIX}4 UBO identification",
            "a natural person marked as UBO, or a ubo_unidentified flag", "neither"))
    return problems


def ubo_problems(pack: KycPack) -> list[str]:
    return _blocking(ubo_findings(pack))


def _norm(s: str) -> str:
    """Whitespace-collapsed text without markdown emphasis/code marks, which models routinely drop when quoting."""
    return " ".join(s.replace("*", "").replace("`", "").split())


def _words(s: str) -> list[str]:
    return re.findall(r"\w+", s.lower())


def _overlap(excerpt: str, text: str) -> float:
    """Share of the excerpt's words that appear in the section text (0 for an empty excerpt)."""
    words, section = _words(excerpt), set(_words(text))
    return sum(w in section for w in words) / len(words) if words else 0.0


def _quoted_in(excerpt: str, text: str, min_overlap: float = MIN_WORD_OVERLAP) -> bool:
    """True when at least `min_overlap` of the excerpt's words appear in the section text. Order, punctuation,
    markdown and '...' are ignored, so a trimmed or reformatted quote passes but an invented rule does not."""
    return _overlap(excerpt, text) >= min_overlap


def _policy_sources(pack: KycPack) -> list:
    """Policy `document` sources cited by at least one risk factor."""
    by_id = {s.id: s for s in pack.sources}
    ids = {sid for f in pack.risk.factors for sid in f.sources}
    return [by_id[sid] for sid in ids if sid in by_id and by_id[sid].reference.startswith(POLICY_PREFIX)]


def citation_findings(pack: KycPack, sections: dict[str, str], min_overlap: float = MIN_WORD_OVERLAP) -> list[Problem]:
    """The rating must cite the policy clause behind it: when factors carry points, some factor must cite section 8.
    Every cited policy source must be a real policy section (`sections`, by `kyc_policy.md#N`), and its excerpt must
    be quoted from that section's text: below `min_overlap` of shared words blocks; a loose quote above it is advisory."""
    if not sections:
        return [Problem("risk: no policy section was retrieved. Call policy_search for the risk rating points system and "
                        "the recommendation rules, and cite the sections you apply.",
                        "policy", "risk", f"{POLICY_PREFIX}8", "policy sections to verify the citations against",
                        "none available")]
    cited = _policy_sources(pack)
    problems = []
    if any(f.points for f in pack.risk.factors) and not any(s.reference == f"{POLICY_PREFIX}8" for s in cited):
        problems.append(Problem(
            "risk: the rating comes from the points table, so cite policy section 8 (a 'document' source "
            "whose reference is the `source` returned by policy_search, e.g. 'kyc_policy.md#8') on the "
            "risk factors that use it.",
            "policy", "risk.factors", f"{POLICY_PREFIX}8 the points table", "a factor citing kyc_policy.md#8",
            "no factor cites it"))
    for src in sorted(cited, key=lambda s: s.id):
        text = sections.get(src.reference)
        path = f"sources[{src.id}].excerpt"
        if text is None:
            problems.append(Problem(f"sources: '{src.id}' cites '{src.reference}', which policy_search did not return.",
                                    "policy", f"sources[{src.id}].reference", "a cited policy section must exist",
                                    f"one of {sorted(sections)}", src.reference))
            continue
        excerpt = src.excerpt or ""
        share = _overlap(excerpt, text)
        if share < min_overlap:
            problems.append(Problem(
                f"sources: '{src.id}' excerpt is not quoted verbatim from the text of "
                f"'{src.reference}' returned by policy_search: fewer than {min_overlap:.0%} of its "
                f"words appear in that text. Copy the rule from that text. "
                f"Do not call policy_search yourself if you are the orchestrator: keep the evaluator's "
                f"excerpt unchanged or ask the evaluator for a corrected one.",
                "policy", path, f"{src.reference} quoted from the policy",
                f"a span quoted from {src.reference}", f"{excerpt[:120]!r} ({share:.0%} of words found)"))
        elif _norm(excerpt) not in _norm(text):
            problems.append(Problem(
                f"sources: '{src.id}' excerpt is not an exact quote of '{src.reference}' "
                f"({share:.0%} of its words appear in the section, but it is not a literal span).",
                "policy", path, f"{src.reference} quoted from the policy",
                f"a literal span of {src.reference}", f"{excerpt[:120]!r}", blocking=False))
    return problems


def citation_problems(pack: KycPack, retrieved: dict[str, str]) -> list[str]:
    return _blocking(citation_findings(pack, retrieved))


def _section9_finding(pack: KycPack) -> list[Problem]:
    if any(s.reference == f"{POLICY_PREFIX}9" for s in _policy_sources(pack)):
        return []
    return [Problem("risk: the recommendation does not cite policy section 9 on any risk factor.", "policy",
                    "risk.recommendation", f"{POLICY_PREFIX}9 the recommendation rule",
                    "a risk factor citing kyc_policy.md#9", "none does", blocking=False)]


def risk_warnings(pack: KycPack) -> list[str]:
    """Non-blocking gaps: the recommendation should cite policy section 9, but the pack stays valid without it."""
    return [p.message for p in _section9_finding(pack)]


def risk_findings(pack: KycPack, sections: dict[str, str] | None = None,
                  min_overlap: float = MIN_WORD_OVERLAP) -> list[Problem]:
    """Violations of the policy's points table and recommendation rule that can be verified from the pack.
    When `sections` (policy text by source) is given, policy citations are enforced too."""
    risk, problems = pack.risk, []
    if sections is not None:
        problems += citation_findings(pack, sections, min_overlap)
    points = sum(f.points or 0 for f in risk.factors)
    want = expected_rating(points)
    if risk.rating != want:
        problems.append(Problem(
            f"risk: the factors add up to {points} points, which is '{want}' under policy section 8, "
            f"but the rating is '{risk.rating}'.",
            "policy", "risk.rating", f"{POLICY_PREFIX}8 the rating bands", want, risk.rating))
    flags = {f.type for f in pack.flags}
    if flags & {"inactive_company", "prohibited_sector"}:
        if risk.recommendation != "decline":
            problems.append(Problem(
                "risk: an inactive company or prohibited sector means 'decline' under policy section 9.",
                "policy", "risk.recommendation", f"{POLICY_PREFIX}9 decline conditions", "decline",
                risk.recommendation))
    elif risk.recommendation == "decline":
        # Only a high-severity sanctions article justifies decline otherwise; the pack must show an adverse_media flag.
        if "adverse_media" not in flags:
            problems.append(Problem(
                "risk: 'decline' needs an inactive company, a prohibited sector or sanctions-related "
                "adverse media, and none is flagged.",
                "policy", "risk.recommendation", f"{POLICY_PREFIX}9 decline conditions",
                "an inactive_company, prohibited_sector or adverse_media flag", "none", cause="unsupported_claim"))
    elif risk.rating == "low" and risk.recommendation != "approve":
        problems.append(Problem(
            "risk: a low rating with no decline condition is 'approve' under policy section 9.",
            "policy", "risk.recommendation", f"{POLICY_PREFIX}9 approve when low", "approve", risk.recommendation))
    elif risk.rating != "low" and risk.recommendation == "approve":
        problems.append(Problem(
            f"risk: a '{risk.rating}' rating cannot be 'approve' under policy section 9.",
            "policy", "risk.recommendation", f"{POLICY_PREFIX}9 approve only when low", "investigate_further",
            risk.recommendation))
    return problems + _section9_finding(pack)


def risk_problems(pack: KycPack, retrieved: dict[str, str] | None = None) -> list[str]:
    """Blocking violations as plain strings. When `retrieved` (policy text by source) is given, citations are enforced."""
    return _blocking(risk_findings(pack, retrieved))
