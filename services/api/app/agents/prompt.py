"""Builds the agent's system prompt: the template plus the example pack. The KYC policy is not injected: the agent reads it with `policy_search`."""
from __future__ import annotations

from pathlib import Path

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


def build_system_prompt() -> str:
    return (
        (PROMPTS / "orchestrator.md").read_text()
        .replace("{{EXAMPLE_PACK}}", (PROMPTS / "example_pack.json").read_text().strip())
    )
