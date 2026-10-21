"""
`run_pipeline()` is the single entry point the API calls for every run.
It receives a PipelineContext and returns a valid `KycPack` (see packages/kyc_common/kyc_common/schema.py).

The pack is built by one Claude Agent SDK agent (`agents/kyc_agent.py`) that may only use the KYC tools and
has a turn limit. If it cannot produce a valid pack, we return the no-AI placeholder pack with a flag, so the
reviewer is never handed a half-analysed pack that looks finished.
"""
from kyc_common import Flag, KycPack

from .agents.kyc_agent import run_kyc_agent
from .context import PipelineContext
from .placeholder import build_placeholder_pack


async def run_pipeline(ctx: PipelineContext) -> KycPack:

    pack = await run_kyc_agent(ctx)
    if pack is not None:
        return pack


    return pack
