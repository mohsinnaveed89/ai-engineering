"""The KYC agent: one Agent SDK session that may only use the eight KYC tools and the country Skills, with a turn limit.

The pack comes back as structured output (KycPack JSON schema). It is validated with Pydantic and the
"no source, no claim" check; on a problem the agent gets the errors back once, in the same session.
"""
from __future__ import annotations

import json
from pathlib import Path

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage, ToolResultBlock, ToolUseBlock, UserMessage)
from pydantic import ValidationError

from kyc_common import KycPack, sourcing_problems

from ..context import PipelineContext
from .prompt import build_system_prompt
from .risk_check import risk_problems, risk_warnings, ubo_problems

AGENT_HOME = Path(__file__).resolve().parent.parent / "agent_home"
MAX_TURNS = 20  # 8 tools, a holding chain or two, news, and a margin. Tune from traces.


def build_options(ctx: PipelineContext) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=build_system_prompt(),
        model=ctx.settings.claude_model,
        cwd=AGENT_HOME,                        # Skills in agent_home/.claude/skills (KYC procedure per country)
        skills="all",
        mcp_servers={"kyc": {"type": "http", "url": ctx.settings.mcp_url}},
        tools=[],                              # no built-in tools: no Bash, Read, Write, web (skills="all" adds Skill)
        allowed_tools=["mcp__kyc__*"],         # only the KYC tools
        max_turns=MAX_TURNS,
        output_format={"type": "json_schema", "schema": KycPack.model_json_schema()},
    )


def build_user_message(ctx: PipelineContext) -> str:
    parts = [f"<company>\n<name>{ctx.company_name}</name>\n<country>{ctx.country}</country>\n</company>"]
    if ctx.feedback:
        parts.append(f"<feedback>{ctx.feedback}</feedback>")
    if ctx.previous_pack:
        parts.append(f"<previous_pack>{ctx.previous_pack.model_dump_json()}</previous_pack>")
    parts.append("Build the KYC onboarding pack for this company.")
    return "\n".join(parts)


def _record_policy_result(block: ToolResultBlock, retrieved: dict[str, str]) -> None:
    """Remember the text of each policy section the agent actually received, keyed by its `source`."""
    content = block.content
    if isinstance(content, list):
        content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
    try:
        data = json.loads(content or "")
    except ValueError:
        return
    if isinstance(data, dict) and "source" in data and "text" in data:
        retrieved[data["source"]] = data["text"]


async def _run_turn(client: ClaudeSDKClient, ctx: PipelineContext, prompt: str,
                    retrieved: dict[str, str]) -> ResultMessage | None:
    await client.query(prompt)
    result = None
    policy_calls: set[str] = set()
    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    await ctx.emit("agent", f"Calling {block.name.removeprefix('mcp__kyc__')}")
                    if block.name.endswith("policy_search"):
                        policy_calls.add(block.id)
        elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, ToolResultBlock) and block.tool_use_id in policy_calls:
                    _record_policy_result(block, retrieved)
        elif isinstance(msg, ResultMessage):
            result = msg
            await ctx.record_metrics(cost_usd=msg.total_cost_usd or 0.0, model=ctx.settings.claude_model,
                                     input_tokens=(msg.usage or {}).get("input_tokens", 0),
                                     output_tokens=(msg.usage or {}).get("output_tokens", 0))
    return result


def _check(result: ResultMessage | None, retrieved: dict[str, str]) -> tuple[KycPack | None, str | None]:
    """Returns (pack, None) when valid, else (None, what to tell the agent)."""
    if result is None or result.structured_output is None:
        why = "no result" if result is None else (
            f"subtype={result.subtype} is_error={result.is_error} terminal_reason={result.terminal_reason} "
            f"turns={result.num_turns} result={(result.result or '')[:300]!r}")
        return None, f"You did not return a pack ({why}). Return the KYC pack as structured output."
    try:
        pack = KycPack.model_validate(result.structured_output)
    except ValidationError as e:
        return None, f"The pack does not match the schema: {e}"
    problems = sourcing_problems(pack)
    if problems:
        return None, "The pack breaks 'no source, no claim':\n" + "\n".join(problems)
    problems = ubo_problems(pack)
    if problems:
        return None, "The ownership section does not follow the KYC policy:\n" + "\n".join(problems)
    problems = risk_problems(pack, retrieved)
    if problems:
        return None, "The risk section does not follow the KYC policy:\n" + "\n".join(problems)
    return pack, None


async def run_kyc_agent(ctx: PipelineContext) -> KycPack | None:
    """Returns a valid pack, or None if the agent could not produce one (caller falls back)."""
    retrieved: dict[str, str] = {}  # policy sections returned by policy_search, kept across the retry
    async with ClaudeSDKClient(options=build_options(ctx)) as client:
        result = await _run_turn(client, ctx, build_user_message(ctx), retrieved)
        pack, problem = _check(result, retrieved)
        if pack is None and result is not None and result.terminal_reason != "max_turns":
            await ctx.emit("agent", "Pack invalid, asking the agent to fix it once")
            result = await _run_turn(client, ctx, f"{problem}\nReturn the corrected pack.", retrieved)
            pack, problem = _check(result, retrieved)
    if pack is None:
        await ctx.emit("agent", f"Agent did not produce a valid pack: {(problem or '')[:200]}")
        return None
    for warning in risk_warnings(pack):
        await ctx.emit("agent", f"Warning: {warning}")
    pack.review.status = "pending"
    pack.generated_by = f"agent-v1 ({ctx.settings.claude_model})"
    return pack
