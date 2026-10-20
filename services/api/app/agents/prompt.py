"""Builds the agent's system prompt: the template plus the KYC policy and the example pack, injected at load time."""
from __future__ import annotations

from pathlib import Path

from ..settings import settings

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
POLICY = Path(settings.data_dir) / "documents" / "policies" / "kyc_policy.md"


def build_system_prompt(policy_path: Path = POLICY) -> str:
    return (
        (PROMPTS / "orchestrator.md").read_text()
        .replace("{{KYC_POLICY}}", policy_path.read_text().strip())
        .replace("{{EXAMPLE_PACK}}", (PROMPTS / "example_pack.json").read_text().strip())
    )
