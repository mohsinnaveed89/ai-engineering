"""
Watch the orchestrator -> news subagent flow: what the orchestrator does, when it starts the subagent,
what the subagent returns, and what the orchestrator returns.

  docker compose exec api python -m app.scripts.test_news_flow "Hawksmoor Meridian Textiles" UK
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import sys

from claude_agent_sdk import AssistantMessage, ClaudeSDKClient, ResultMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage

from kyc_common import KycPack

from ..agents.kyc_agent import build_orchestrator_options, build_user_message
from ..context import PipelineContext

# Loose partial-pack schema (the real KycPack requires company + risk).
PARTIAL = {"type": "object", "required": ["news", "flags", "sources"],
           "properties": {k: {"type": "array", "items": {"type": "object"}} for k in ("news", "flags", "sources")}}


def show(title: str, body) -> None:
    text = body if isinstance(body, str) else json.dumps(body, indent=2, ensure_ascii=False)
    print(f"\n=== {title} ===\n{text}")


async def main(name: str, country: str) -> None:
    ctx = PipelineContext(run_id="newsflow", company_name=name, country=country)
    options = dataclasses.replace(build_orchestrator_options(ctx), output_format={"type": "json_schema", "schema": KycPack.model_json_schema()})
    agent_calls: set[str] = set()  # tool_use ids of subagent launches

    async with ClaudeSDKClient(options=options) as client:
        await client.query(build_user_message(ctx))
        async for msg in client.receive_response():
            # parent_tool_use_id is set when the message comes from inside a subagent
            who = "SUBAGENT" if getattr(msg, "parent_tool_use_id", None) else "ORCHESTRATOR"
            if isinstance(msg, AssistantMessage):
                for b in msg.content:
                    if isinstance(b, TextBlock) and b.text.strip():
                        show(f"{who} says", b.text)
                    elif isinstance(b, ToolUseBlock) and b.name in ("Agent", "Task"):
                        agent_calls.add(b.id)
                        show("ORCHESTRATOR STARTS SUBAGENT", b.input)
                    elif isinstance(b, ToolUseBlock):
                        show(f"{who} calls {b.name.removeprefix('mcp__kyc__')}", b.input)
            elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
                for b in msg.content:
                    if isinstance(b, ToolResultBlock) and b.tool_use_id in agent_calls:
                        show("SUBAGENT RETURNED TO ORCHESTRATOR", b.content)
            elif isinstance(msg, ResultMessage):
                show("ORCHESTRATOR RETURNS", msg.structured_output if msg.structured_output is not None
                     else f"no structured output ({msg.subtype}, {msg.terminal_reason}): {msg.result}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit('usage: python -m app.scripts.test_news_flow "<company name>" <UK|PT|BR>')
    asyncio.run(main(sys.argv[1], sys.argv[2]))
