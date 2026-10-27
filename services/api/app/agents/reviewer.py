"""The reviewer: checks a candidate pack and returns ONE structured `ReviewFeedback` for the orchestrator.

It never edits the pack and never talks to the orchestrator: `run_kyc_agent` calls it after each turn and, on a fail,
sends the feedback back into the same session. This file holds the feedback models, the deterministic code stage
(schema, sourcing, ownership, risk arithmetic, policy citations) and the helpers that render the feedback as a
send-back message or a readable failure summary. The design is in docs/reviewer-design.md.

`ReviewFeedback` lives here, not in kyc_common: the shared pack schema is fixed.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Literal

import psycopg
from pydantic import BaseModel, ValidationError

from kyc_common import KycPack, sourcing_problems

from .review_capture import RunCapture
from .review_checks import (Missing, coverage, flag_producers, log_notes, merge_findings, news_flag_findings,
                            reply_schema_findings, unrequested_changes)
from .risk_check import MIN_WORD_OVERLAP, POLICY_PREFIX, Problem, risk_findings, ubo_findings

Trigger = Literal["auto_review", "human_feedback"]
Verdict = Literal["pass", "fail", "revise"]
Severity = Literal["blocking", "advisory"]
Category = Literal["schema", "sourcing", "flags", "policy", "merge", "human_feedback"]
Agent = Literal["orchestrator", "ownership", "news", "bank", "financials", "evaluator", "unknown"]
Cause = Literal["model_error", "tool_error", "missing_data", "unsupported_claim", "merge_changed"]
Origin = Literal["code", "llm", "human"]

PACK_FIELDS = ("company", "officers", "owners", "news", "financial_summary", "risk", "flags", "sources")
POLICY_DOC_ID = "kyc_policy"


class Finding(BaseModel):
    id: str = ""
    severity: Severity
    category: Category
    path: str = ""                    # e.g. risk.factors[1].sources, sources[eval-policy-8].excerpt
    produced_by: Agent = "unknown"
    rule: str = ""                    # the pack rule or policy section it breaks
    problem: str
    expected: str = ""
    actual: str = ""
    cause: Cause = "model_error"
    suggested_owner: Agent = "orchestrator"   # a suggestion: the orchestrator decides where to route it
    origin: Origin = "code"
    reviewer_comment: str | None = None       # the human's words (human origin only)
    still_unresolved: bool = False            # raised in an earlier version too
    note: str = ""                            # why a finding the reviewer wanted to block stayed a warning


class MissingItem(BaseModel):
    path: str
    required_by: str
    expected: str = ""
    found: str = ""
    status: Literal["absent", "null_without_flag", "unsupported"] = "absent"
    suggested_owner: Agent = "orchestrator"


class Suggestion(BaseModel):
    about: str
    text: str
    based_on: str = ""


class FieldStatus(BaseModel):
    field: str
    status: Literal["ok", "missing", "invalid"]
    reason: str = ""


class ReviewFeedback(BaseModel):
    trigger: Trigger = "auto_review"
    verdict: Verdict
    round: int
    max_sendbacks: int
    fatal: bool = False               # asking again cannot help (no pack, ran out of turns)
    findings: list[Finding] = []
    missing: list[MissingItem] = []
    suggestions: list[Suggestion] = []
    fields: list[FieldStatus] = []    # roll-up per top-level pack field

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "blocking"]

    @property
    def advisory(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "advisory"]


class PackRejected(Exception):
    """The team could not produce a pack that passes review. Carries the reviewer's last feedback."""

    def __init__(self, feedback: ReviewFeedback):
        self.feedback = feedback
        super().__init__(render_summary(feedback))


# Which agent wrote what, from the source-id prefix each agent was told to use and from the pack field.
_PREFIX_OWNER: dict[str, Agent] = {"reg": "orchestrator", "own": "ownership", "news": "news", "bank": "bank",
                                   "fin": "financials", "eval": "evaluator"}
_FIELD_OWNER: dict[str, Agent] = {"company": "orchestrator", "officers": "ownership", "owners": "ownership",
                                  "news": "news", "financial_summary": "financials", "risk": "evaluator"}


def producer_for(path: str) -> Agent:
    """Best guess at which agent produced the thing at `path`. Flags are left unknown: the type decides who raised them."""
    m = re.match(r"sources\[([^\]]+)\]", path)
    if m:
        return _PREFIX_OWNER.get(m.group(1).split("-")[0], "unknown")
    return _FIELD_OWNER.get(re.split(r"[.\[]", path, maxsplit=1)[0], "unknown")


def _finding(p: Problem, flag_owner: dict[str, str]) -> Finding:
    flag = re.match(r"flags\[\d+\]", p.path)
    produced_by = p.produced_by or (flag_owner.get(flag.group(0), "unknown") if flag else producer_for(p.path))
    owner = p.suggested_owner or (produced_by if produced_by != "unknown" else "orchestrator")
    return Finding(severity="blocking" if p.blocking else "advisory", category=p.category,  # type: ignore[arg-type]
                   path=p.path, produced_by=produced_by, rule=p.rule, problem=p.message, expected=p.expected,  # type: ignore[arg-type]
                   actual=p.actual, cause=p.cause, suggested_owner=owner)  # type: ignore[arg-type]


def _missing_item(m: Missing) -> MissingItem:
    return MissingItem(path=m.path, required_by=m.required_by, expected=m.expected, found=m.found,
                       status=m.status, suggested_owner=m.owner)  # type: ignore[arg-type]


def _schema_problems(e: ValidationError) -> list[Problem]:
    return [Problem(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}", "schema",
                    ".".join(str(x) for x in err["loc"]), "the KYC pack schema", err["type"],
                    str(err.get("input"))[:80]) for err in e.errors()]


def _sourcing_problems(pack: KycPack) -> list[Problem]:
    """kyc_common reports 'path: problem' strings; the path is the first word before the colon."""
    return [Problem(p, "sourcing", p.split(": ", 1)[0].split(" ")[0], "no source, no claim",
                    "every fact cites a source id that exists in `sources`", p.split(": ", 1)[-1],
                    cause="unsupported_claim") for p in sourcing_problems(pack)]


def _roll_up(findings: list[Finding], missing: list[MissingItem]) -> list[FieldStatus]:
    invalid: dict[str, str] = {}
    absent: dict[str, str] = {}
    for f in findings:
        top = re.split(r"[.\[]", f.path, maxsplit=1)[0]
        if f.severity == "blocking" and top in PACK_FIELDS:
            invalid.setdefault(top, f.problem)
    for m in missing:
        absent.setdefault(re.split(r"[.\[]", m.path, maxsplit=1)[0], f"{m.found} ({m.required_by})")
    return [FieldStatus(field=name, status="invalid" if name in invalid else "missing" if name in absent else "ok",
                        reason=invalid.get(name) or absent.get(name, "")) for name in PACK_FIELDS]


def review_output(structured_output: dict | None, *, sections: dict[str, str], round_no: int, max_sendbacks: int,
                  no_pack_reason: str | None = None, fatal: bool = False, min_overlap: float = MIN_WORD_OVERLAP,
                  trigger: Trigger = "auto_review", suggestions: list[Suggestion] | None = None,
                  capture: RunCapture | None = None, expect_all_specialists: bool = False,
                  previous_pack: KycPack | None = None,
                  requested: set[str] | None = None) -> tuple[ReviewFeedback, KycPack | None]:
    """The code stage. Returns the feedback and, when nothing blocks, the validated pack.
    `capture` (what each specialist returned and every tool call) enables the merge and tool-log checks;
    `expect_all_specialists` is true for a fresh run, where every specialist should have run. In a human revision,
    `previous_pack` and `requested` (the top-level fields the human's comment names) enable the unrequested-change check."""
    problems: list[Problem] = []
    missing: list[Missing] = []
    flag_owner: dict[str, str] = {}
    notes = list(suggestions or [])
    pack: KycPack | None = None
    if structured_output is None:
        problems.append(Problem(f"You did not return a pack ({no_pack_reason or 'no result'}). "
                                "Return the KYC pack as structured output.", "schema", "", "a pack is returned",
                                "a KycPack as structured output", no_pack_reason or "nothing"))
    else:
        try:
            pack = KycPack.model_validate(structured_output)
        except ValidationError as e:
            problems += _schema_problems(e)
    if pack is not None:
        problems += (_sourcing_problems(pack) + ubo_findings(pack) + risk_findings(pack, sections, min_overlap)
                     + news_flag_findings(pack))
        missing = coverage(pack, capture, expect_all_specialists)
        if capture is not None:
            problems += merge_findings(pack, capture) + reply_schema_findings(capture)
            flag_owner = flag_producers(pack, capture)
            notes += [Suggestion(about=n.about, text=n.text, based_on=n.based_on) for n in log_notes(capture)]
        if previous_pack is not None:
            problems += unrequested_changes(previous_pack, pack, requested or set())
    findings = [_finding(p, flag_owner) for p in problems]
    for i, f in enumerate(findings, 1):
        f.id = f"F{i}"
    missing_items = [_missing_item(m) for m in missing]
    failed = any(f.severity == "blocking" for f in findings) or bool(missing_items)
    feedback = ReviewFeedback(trigger=trigger, verdict="fail" if failed else "pass", round=round_no,
                              max_sendbacks=max_sendbacks, fatal=failed and fatal, findings=findings,
                              missing=missing_items, suggestions=notes,
                              fields=_roll_up(findings, missing_items) if pack else [])
    return feedback, None if failed else pack


def is_valid_pack(structured_output: dict | None) -> bool:
    """True when the output is a pack that validates against the schema, i.e. there is something to review."""
    if structured_output is None:
        return False
    try:
        KycPack.model_validate(structured_output)
    except ValidationError:
        return False
    return True


def _policy_sections_sync(database_url: str) -> dict[str, str]:
    """Each numbered policy section's text from the vector DB, keyed like policy_search's `source` ('kyc_policy.md#8')."""
    with psycopg.connect(database_url) as conn:
        rows = conn.execute("SELECT text, metadata FROM document_chunks WHERE doc_id = %s ORDER BY chunk_idx",
                            (POLICY_DOC_ID,)).fetchall()
    out: dict[str, list[str]] = {}
    for text, metadata in rows:
        m = re.match(r"(\d+)\.", (metadata or {}).get("section") or "")
        if m:  # the title preamble has no number to cite
            out.setdefault(f"{POLICY_PREFIX}{m.group(1)}", []).append(text)
    return {source: "\n\n".join(texts) for source, texts in out.items()}


async def policy_ground_truth(database_url: str, retrieved: dict[str, str]) -> tuple[dict[str, str], list[Suggestion]]:
    """Policy text to verify citations against: the DB sections (the truth) over what the agents retrieved. If the
    DB cannot be read, fall back to what they retrieved and say that verification was weaker."""
    try:
        return {**retrieved, **await asyncio.to_thread(_policy_sections_sync, database_url)}, []
    except (psycopg.Error, OSError) as e:
        return dict(retrieved), [Suggestion(
            about="policy verification",
            text="The policy sections could not be loaded from the database, so citations were checked only against "
                 "the text the agents retrieved with policy_search.",
            based_on=f"{type(e).__name__}: {str(e)[:120]}")]


def render_summary(feedback: ReviewFeedback) -> str:
    """Readable multi-line summary, for the run's error text and the CLI."""
    n = feedback.round
    lines = [f"Pack failed review after {n} round{'s' if n != 1 else ''} "
             f"({len(feedback.blocking)} blocking, {len(feedback.missing)} missing, {len(feedback.advisory)} advisory)."]
    for f in feedback.blocking:
        problem = re.sub(r"^[a-z_]+: ", "", f.problem)   # the path already says where
        problem = problem.split(" Do not call policy_search")[0]   # a hint for the orchestrator, noise for a person
        lines.append(f"  [{f.produced_by}] {f.path or 'pack'}: {problem}")
    for item in feedback.missing:
        lines.append(f"  Missing: {item.path} ({item.required_by}): {item.found}; suggested owner: {item.suggested_owner}")
    bad = [f"{s.field} ({s.reason[:60]})" for s in feedback.fields if s.status != "ok"]
    if bad:
        lines.append("Fields not generated correctly: " + "; ".join(bad))
    for s in feedback.suggestions:
        lines.append(f"  Suggestion: {s.text}")
    return "\n".join(lines)


def send_back_message(feedback: ReviewFeedback) -> str:
    """What the orchestrator receives when a pack fails review. The reviewer only suggests an owner per finding;
    the orchestrator decides where each one goes."""
    payload = feedback.model_dump(mode="json", exclude={"fields"})
    return (
        f"The reviewer checked your pack (round {feedback.round}) and it did not pass.\n"
        f"<review_feedback>{json.dumps(payload, ensure_ascii=False)}</review_feedback>\n"
        "Each blocking finding names the pack `path`, the agent that most likely produced it (`produced_by`) and a "
        "`suggested_owner`; each `missing` item says what the policy requires and which agent should supply it. The "
        "suggestions are not binding. For each blocking finding and missing item decide which subagent, if any, must "
        "be run again, and pass it that finding in its prompt. Change nothing else in the pack. Do not call "
        "`policy_search` yourself: keep policy sources exactly as the evaluator returned them, or ask the evaluator "
        "for a corrected one. Advisory findings and suggestions need no action. Return the full corrected pack.")
