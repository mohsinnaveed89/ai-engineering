"""The reviewer's human mode: triage of a send-back comment.

Before the orchestrator revises a pack, the reviewer maps each point of the human's comment to the pack paths it
concerns, a suggested owner, and whether the team's sources can support it at all. The result is a `ReviewFeedback` with
verdict `revise` and one `human_feedback` finding per (point, path). The orchestrator receives it next to the raw
comment and decides the routing; the same paths tell the code stage which sections the revision may change
(`requested_sections`). Like the LLM review stage, triage never takes the run down: if it cannot answer, the comment
goes through as one finding and the unrequested-change check is switched off. See docs/reviewer-design.md, section 6."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Literal

from claude_agent_sdk import ClaudeSDKClient
from pydantic import BaseModel, ValidationError

from ..context import PipelineContext
from .review_checks import ANY_SECTION
from .reviewer import Agent, Finding, ReviewFeedback, Suggestion, producer_for
from .reviewer_llm import _converse, _section_order, build_reviewer_options
from .risk_check import POLICY_PREFIX

PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / "reviewer_triage.md"
MAX_COMMENT_CHARS = 4000


class TriagePoint(BaseModel):
    comment: str                       # the part of the human's comment this point comes from, as written
    request: str                       # one neutral sentence: what is being asked
    paths: list[str] = []              # pack paths it concerns; empty when none can be named
    suggested_owner: Agent = "orchestrator"
    status: Literal["actionable", "ambiguous", "unsupported"] = "actionable"
    note: str = ""                     # why ambiguous or unsupported; what source would be needed
    raised_before: bool = False        # the human asked for this in an earlier version too


class LlmTriage(BaseModel):
    points: list[TriagePoint] = []
    summary: str = ""


def build_triage_prompt(team: str, sections: dict[str, str]) -> str:
    policy = "\n\n".join(f"[{source}]\n{text}" for source, text in sorted(sections.items(), key=lambda kv: _section_order(kv[0]))
                         if source.startswith(POLICY_PREFIX)) or "(not available)"
    return PROMPT_FILE.read_text().replace("{{TEAM}}", team).replace("{{POLICY}}", policy)


def build_triage_message(ctx: PipelineContext) -> str:
    earlier = [{"version": r.get("version"), "decision": r.get("decision"), "comments": r.get("comments")}
               for r in ctx.review_history[:-1] if r.get("comments")]  # the last entry is this send-back
    pack = ctx.previous_pack.model_dump_json(exclude={"review", "generated_at"}) if ctx.previous_pack else "null"
    return (f"<comment>{(ctx.feedback or '')[:MAX_COMMENT_CHARS]}</comment>\n"
            f"<earlier_decisions>{json.dumps(earlier, ensure_ascii=False)}</earlier_decisions>\n"
            f"<previous_pack>{pack}</previous_pack>\n"
            "Triage this comment as instructed and return the points.")


async def run_llm_triage(ctx: PipelineContext, sections: dict[str, str],
                         team: str) -> tuple[LlmTriage | None, str | None]:
    """Returns (triage, None), or (None, why) when it could not complete. It never raises."""
    try:
        options = build_reviewer_options(ctx, build_triage_prompt(team, sections), LlmTriage)
        async with ClaudeSDKClient(options=options) as client:
            result = await asyncio.wait_for(_converse(client, ctx, build_triage_message(ctx)),
                                            timeout=ctx.settings.reviewer_timeout_s)
    except asyncio.TimeoutError:
        return None, f"timed out after {ctx.settings.reviewer_timeout_s}s"
    except Exception as e:  # noqa: BLE001 - the optional stage reports, never raises
        return None, f"{type(e).__name__}: {str(e)[:150]}"
    if result is None or result.structured_output is None:
        return None, f"no structured output ({'no result' if result is None else result.subtype})"
    try:
        return LlmTriage.model_validate(result.structured_output), None
    except ValidationError as e:
        return None, f"invalid output: {e.error_count()} error(s), first at {e.errors()[0]['loc']}"


def _top(path: str) -> str:
    return re.split(r"[.\[]", path, maxsplit=1)[0]


def triage_feedback(comment: str, triage: LlmTriage | None, max_sendbacks: int,
                    error: str | None = None) -> ReviewFeedback:
    """The triage as a `ReviewFeedback` (verdict `revise`). An actionable point is blocking: the revision has to deal
    with it. An ambiguous or unsupported one is advisory: it is shown to the orchestrator and the approver, not
    enforced. Without a usable triage the whole comment becomes one finding with no path."""
    suggestions: list[Suggestion] = []
    findings: list[Finding] = []
    if triage is None or not triage.points:
        findings.append(Finding(
            severity="blocking", category="human_feedback", origin="human", reviewer_comment=comment,
            problem=comment[:300], suggested_owner="orchestrator", cause="missing_data",
            note="the comment could not be mapped to the pack"))
        suggestions.append(Suggestion(
            about="triage", based_on=error or "",
            text="The reviewer could not triage the comment, so it is passed on as written and every section may "
                 "change." if triage is None else "The reviewer found no actionable point in the comment."))
    else:
        for point in triage.points:
            for path in point.paths or [""]:
                findings.append(Finding(
                    id=f"F{len(findings) + 1}", severity="blocking" if point.status == "actionable" else "advisory",
                    category="human_feedback", path=path,
                    produced_by=producer_for(path) if path else "unknown", rule="human send-back",
                    problem=point.request, cause="missing_data", suggested_owner=point.suggested_owner,
                    origin="human", reviewer_comment=point.comment, still_unresolved=point.raised_before,
                    note=f"{point.status}: {point.note}".rstrip(": ") if point.status != "actionable" else point.note))
        if triage.summary:
            suggestions.append(Suggestion(about="triage", text=triage.summary, based_on="reviewer triage"))
    for i, f in enumerate(findings, 1):
        f.id = f"F{i}"
    return ReviewFeedback(trigger="human_feedback", verdict="revise", round=0, max_sendbacks=max_sendbacks,
                          findings=findings, suggestions=suggestions)


def requested_sections(feedback: ReviewFeedback) -> set[str]:
    """Top-level pack fields the revision may change: those named by an actionable or ambiguous point (an ambiguous one
    may need the agent to look, and the look may find something). Unsupported points request nothing. ANY_SECTION when
    such a point names no path, or when triage failed, since nothing can then be judged unrequested."""
    open_points = [f for f in feedback.findings if f.origin == "human" and not f.note.startswith("unsupported")]
    if any(not f.path for f in open_points):
        return {ANY_SECTION}
    return {_top(f.path) for f in open_points}


def triage_message(feedback: ReviewFeedback) -> str:
    """The reviewer's triage as the orchestrator receives it, inside the first message of a revision."""
    payload = feedback.model_dump(mode="json", exclude={"fields", "missing", "round"})
    return f"<review_feedback>{json.dumps(payload, ensure_ascii=False)}</review_feedback>"
