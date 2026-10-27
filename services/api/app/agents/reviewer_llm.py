"""The reviewer's LLM stage: judgement the code stage cannot make (typed flags against the evidence, risk factors against
flags, the rationale, gaps the specialists left unflagged, who caused what).

It runs as its own session, separate from the orchestrator team: no built-in tools, read-only `policy_search` and
`bank_get_risk_appetite`, and a structured output (`LlmReview`). `apply_llm_review` folds that output into the code
stage's `ReviewFeedback`, and decides what may block: only a finding in the flags or policy category that cites evidence
and a concrete contradiction. Everything else is shown as a warning. See docs/reviewer-design.md."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Literal

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher, ResultMessage,
                              ToolResultBlock, ToolUseBlock, UserMessage)
from pydantic import BaseModel, ValidationError

from kyc_common import KycPack

from ..context import PipelineContext
from .review_capture import RunCapture, flatten_content
from .reviewer import (Agent, Category, Cause, Finding, MissingItem, ReviewFeedback, Suggestion, _roll_up)
from .risk_check import POLICY_PREFIX

PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / "reviewer.md"
AGENT_HOME = Path(__file__).resolve().parent.parent / "agent_home"
ALLOWED_TOOLS = ["mcp__kyc__policy_search", "mcp__kyc__bank_get_risk_appetite"]
OUTPUT_TOOL = "StructuredOutput"  # the SDK's own tool for delivering the structured output, not a capability to limit
REVIEWER_MAX_TURNS = 8
MAX_TOOL_CALLS_SHOWN = 60
BLOCKING_CATEGORIES = ("flags", "policy")


class LlmFinding(BaseModel):
    category: Category
    path: str
    produced_by: Agent = "unknown"
    rule: str = ""
    problem: str
    expected: str = ""
    actual: str = ""
    cause: Cause = "model_error"
    suggested_owner: Agent = "orchestrator"
    evidence: list[str] = []       # pack source ids, or policy sections like kyc_policy.md#6, that show the problem
    contradiction: str = ""        # the concrete fact that contradicts the pack
    blocking: bool = False         # what the reviewer asks for; apply_llm_review decides


class LlmMissing(BaseModel):
    path: str
    required_by: str
    expected: str = ""
    found: str = ""
    status: Literal["absent", "null_without_flag", "unsupported"] = "absent"
    suggested_owner: Agent = "orchestrator"
    evidence: list[str] = []


class LlmSuggestion(BaseModel):
    about: str
    text: str


class LlmReview(BaseModel):
    findings: list[LlmFinding] = []
    missing: list[LlmMissing] = []
    suggestions: list[LlmSuggestion] = []
    summary: str = ""


# --- the prompt -----------------------------------------------------------------------------------------------------

def describe_team(subagents: dict[str, Any]) -> str:
    """The specialists as the orchestrator actually has them (description, tools, turn limit, the source-id prefix and
    reply keys from their own prompts), so the reviewer's picture of the team cannot drift from the code."""
    lines = []
    for name, agent in subagents.items():
        prefix = re.search(r"source id you define with '(\w+)-'", agent.prompt)
        keys = re.search(r"with these keys: ([^.]+)\.", agent.prompt)
        tools = ", ".join(t.rsplit("__", 1)[-1] for t in agent.tools)
        lines.append(f"- `{name}`: {agent.description} Source ids `{prefix.group(1) + '-' if prefix else '?'}`. "
                     f"Replies with: {keys.group(1) if keys else '?'}. Tools: {tools}. Up to {agent.maxTurns} turns.")
    return "\n".join(lines)


def _section_order(source: str) -> int:
    return int(source.rsplit("#", 1)[-1])


def build_system_prompt(team: str, sections: dict[str, str]) -> str:
    policy = "\n\n".join(f"[{source}]\n{text}" for source, text in sorted(sections.items(), key=lambda kv: _section_order(kv[0]))
                         if source.startswith(POLICY_PREFIX)) or "(not available: use policy_search)"
    return PROMPT_FILE.read_text().replace("{{TEAM}}", team).replace("{{POLICY}}", policy)


def revision_note(ctx: PipelineContext) -> str:
    """Tells the reviewer this pack is a revision after a human send-back, so what it sees is partial by design."""
    if not (ctx.feedback and ctx.previous_pack):
        return ""
    return (f"<revision>This pack is a revision after a human send-back; the comment was: {ctx.feedback[:1000]!r}. "
            "Only the specialists that were re-run appear in `<tool_calls>` and `<specialist_replies>`: every other "
            "section was kept from the previous pack on purpose and was reviewed in an earlier version, so do not "
            "report missing calls or replies for it. The team's answer to the comment belongs in the pack's `notes`. "
            "A flag or source that refers to the send-back, the comment or the reviewer's point, instead of reporting "
            "evidence, is a defect (category `flags`, advisory). Ordinary wording such as asking the approver to "
            "confirm a match is not: judge only text that responds to the comment.</revision>\n")


def build_user_message(structured_output: dict, capture: RunCapture, code_feedback: ReviewFeedback,
                       revision: str = "") -> str:
    calls = [{"agent": c.agent, "tool": c.tool, "input": c.input, "error": c.is_error, "result_chars": c.result_chars}
             for c in capture.tool_calls[:MAX_TOOL_CALLS_SHOWN]]
    code = code_feedback.model_dump(mode="json", include={"findings", "missing", "suggestions"})
    dump = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)  # noqa: E731
    return (f"<pack>{dump(structured_output)}</pack>\n"
            f"<specialist_replies>{dump(capture.subagents)}</specialist_replies>\n"
            f"<tool_calls>{dump(calls)}</tool_calls>\n"
            f"<code_review>{dump(code)}</code_review>\n"
            f"{revision}"
            "Review this pack as instructed and return your findings.")


# --- the session ----------------------------------------------------------------------------------------------------

async def _reviewer_tool_guard(input_data, tool_use_id, context):
    """The reviewer may use exactly its two read-only tools, plus the SDK's output tool to deliver its answer."""
    name = input_data.get("tool_name", "")
    if name in ALLOWED_TOOLS or name == OUTPUT_TOOL:
        return {}
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": f"{name} is not available to the reviewer. You may only use "
                                    f"{', '.join(t.rsplit('__', 1)[-1] for t in ALLOWED_TOOLS)}."}}


def build_reviewer_options(ctx: PipelineContext, system_prompt: str,
                           output_model: type[BaseModel] = LlmReview) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=ctx.settings.reviewer_model,
        cwd=AGENT_HOME,
        tools=[],                                                     # no built-in tools at all
        allowed_tools=ALLOWED_TOOLS,
        mcp_servers={"kyc": {"type": "http", "url": ctx.settings.mcp_url}},
        hooks={"PreToolUse": [HookMatcher(matcher=".*", hooks=[_reviewer_tool_guard])]},
        env={"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"},
        max_turns=REVIEWER_MAX_TURNS,
        output_format={"type": "json_schema", "schema": output_model.model_json_schema()},
    )


async def _converse(client: ClaudeSDKClient, ctx: PipelineContext, message: str) -> ResultMessage | None:
    await client.query(message)
    result = None
    structured_calls: dict[str, int] = {}  # StructuredOutput tool_use id -> payload size, to log why it was refused
    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    await ctx.emit("reviewer", f"LLM review calling {block.name.removeprefix('mcp__kyc__')}")
                    if block.name == "StructuredOutput":
                        structured_calls[block.id] = len(json.dumps(block.input, default=str))
        elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, ToolResultBlock) and block.tool_use_id in structured_calls:
                    size = structured_calls.pop(block.tool_use_id)
                    await ctx.emit("debug", f"reviewer StructuredOutput ({size} chars) -> "
                                            f"{'rejected' if block.is_error else 'accepted'}: "
                                            f"{flatten_content(block.content)[:400]}")
        elif isinstance(msg, ResultMessage):
            result = msg
            await ctx.record_metrics(cost_usd=msg.total_cost_usd or 0.0, model=ctx.settings.reviewer_model,
                                     input_tokens=(msg.usage or {}).get("input_tokens", 0),
                                     output_tokens=(msg.usage or {}).get("output_tokens", 0))
    return result


async def run_llm_review(ctx: PipelineContext, structured_output: dict, capture: RunCapture,
                         code_feedback: ReviewFeedback, sections: dict[str, str],
                         team: str) -> tuple[LlmReview | None, str | None]:
    """Returns (review, None), or (None, why) when the LLM stage could not complete. It never raises: a reviewer that
    cannot answer must not take the run down, so the code stage's verdict stands."""
    try:
        async with ClaudeSDKClient(options=build_reviewer_options(ctx, build_system_prompt(team, sections))) as client:
            result = await asyncio.wait_for(
                _converse(client, ctx, build_user_message(structured_output, capture, code_feedback, revision_note(ctx))),
                timeout=ctx.settings.reviewer_timeout_s)
    except asyncio.TimeoutError:
        return None, f"timed out after {ctx.settings.reviewer_timeout_s}s"
    except Exception as e:  # noqa: BLE001 - any failure of the optional stage is reported, not raised
        return None, f"{type(e).__name__}: {str(e)[:150]}"
    if result is None or result.structured_output is None:
        return None, f"no structured output ({'no result' if result is None else result.subtype})"
    try:
        return LlmReview.model_validate(result.structured_output), None
    except ValidationError as e:
        return None, f"invalid output: {e.error_count()} error(s), first at {e.errors()[0]['loc']}"


# --- folding the LLM's output into the feedback ------------------------------------------------------------------------

def _valid_evidence(evidence: list[str], pack: KycPack) -> bool:
    known = {s.id for s in pack.sources}
    return any(e in known or re.fullmatch(re.escape(POLICY_PREFIX) + r"\d+", e) for e in evidence)


def apply_llm_review(feedback: ReviewFeedback, llm: LlmReview | None, structured_output: dict,
                     error: str | None = None, can_block: bool = True) -> ReviewFeedback:
    """Add the LLM stage's output to the code stage's feedback. With `can_block` off (the default setting) every LLM
    finding is a warning. With it on, a finding blocks only when it is in the flags or policy category and cites real
    evidence and a concrete contradiction, and a missing item needs evidence or it becomes a suggestion. A finding that
    asked to block but did not carries a `note` saying why. Duplicates of code findings are dropped. If the stage failed,
    say so and keep the code stage's verdict."""
    suggestions = list(feedback.suggestions)
    if llm is None:
        suggestions.append(Suggestion(about="LLM review", text="The LLM review could not be completed, so only the code "
                                      "checks ran.", based_on=error or ""))
        return feedback.model_copy(update={"suggestions": suggestions})
    pack = KycPack.model_validate(structured_output)
    findings = list(feedback.findings)
    seen = {(f.path, f.category) for f in findings}
    for item in llm.findings:
        if (item.path, item.category) in seen:
            continue
        seen.add((item.path, item.category))
        why = ""
        if not can_block:
            why = "LLM findings are advisory (REVIEW_LLM_BLOCKS=0)"
        elif item.category not in BLOCKING_CATEGORIES:
            why = f"category '{item.category}' cannot block"
        elif not item.contradiction.strip():
            why = "no concrete contradiction given"
        elif not _valid_evidence(item.evidence, pack):
            why = "evidence is not a pack source or a policy section"
        blocks = item.blocking and not why
        findings.append(Finding(note=f"asked to block, kept as a warning: {why}" if item.blocking and why else "",
                                
            id=f"F{len(findings) + 1}", severity="blocking" if blocks else "advisory", category=item.category,
            path=item.path, produced_by=item.produced_by, rule=item.rule, problem=item.problem,
            expected=item.expected, actual=item.actual or item.contradiction, cause=item.cause,
            suggested_owner=item.suggested_owner, origin="llm"))
    missing = list(feedback.missing)
    have = {m.path for m in missing}
    for item in llm.missing:
        if item.path in have:
            continue
        if can_block and item.evidence and _valid_evidence(item.evidence, pack):
            missing.append(MissingItem(path=item.path, required_by=item.required_by, expected=item.expected,
                                       found=item.found, status=item.status, suggested_owner=item.suggested_owner))
        else:
            suggestions.append(Suggestion(about=f"possible gap: {item.path}",
                                          text=f"{item.found or item.expected} ({item.required_by})",
                                          based_on="LLM review, " + ("no evidence cited" if can_block
                                                                     else "advisory")))
    suggestions += [Suggestion(about=s.about, text=s.text, based_on="LLM review") for s in llm.suggestions]
    failed = any(f.severity == "blocking" for f in findings) or bool(missing)
    return feedback.model_copy(update={
        "findings": findings, "missing": missing, "suggestions": suggestions,
        "verdict": "fail" if failed else "pass", "fields": _roll_up(findings, missing)})
