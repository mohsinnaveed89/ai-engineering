"""
=====================================================================
  THIS IS WHERE YOUR AGENTS GO.
=====================================================================

`run_pipeline()` is the single entry point the API calls for every run.
It receives a PipelineContext and must return a valid `KycPack`
(see packages/kyc_common/kyc_common/schema.py).

Today it calls a placeholder that builds a pack straight from the mock data,
with NO AI: it copies register fields, lists news without judging it, and
always recommends "investigate_further". Replace the body with your own
pipeline (Claude Agent SDK, specialist agents, reviewer loop, RAG, MCP...).

Useful things on `ctx`:
  ctx.company_name, ctx.country       what the analyst submitted
  ctx.feedback, ctx.previous_pack     set when the human sent the pack back
  await ctx.emit(step, message)       live progress in the web app
  ctx.settings                        URLs and keys from .env

The API validates what you return against the schema, checks every fact has
a source, stores it and asks a human to review it.
"""
from kyc_common import KycPack

from .context import PipelineContext
from .placeholder import build_placeholder_pack


async def run_pipeline(ctx: PipelineContext) -> KycPack:
    # TODO (Day 1 onwards): replace the placeholder with your own pipeline.
    return await build_placeholder_pack(ctx)
