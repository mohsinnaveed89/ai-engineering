"""
`run_pipeline()` is the single entry point the API calls for every run.
It receives a PipelineContext and returns a valid `KycPack` (see packages/kyc_common/kyc_common/schema.py).

The pack is built by one Claude Agent SDK agent (`agents/kyc_agent.py`) that may only use the KYC tools and
has a turn limit. A reviewer (`agents/reviewer.py`) checks every pack before it is returned. If the team cannot
produce a pack that passes, `PackRejected` is raised with the reviewer's findings, so the reviewer is never
handed a half-analysed pack that looks finished.
"""
from kyc_common import KycPack

# Two alternates: `kyc_agent` (SDK `agents={}` subagents) or `kyc_agent_tools` (specialists wrapped as tools).
from .agents.kyc_agent import run_kyc_agent
# from .agents.kyc_agent_tools import run_kyc_agent
from .context import PipelineContext


async def run_pipeline(ctx: PipelineContext) -> KycPack:
    return await run_kyc_agent(ctx)
