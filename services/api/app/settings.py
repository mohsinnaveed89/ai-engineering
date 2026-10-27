import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "postgresql://academy:academy@localhost:5432/academy"))
    mockdata_url: str = field(default_factory=lambda: os.getenv("MOCKDATA_URL", "http://localhost:8001"))
    mcp_url: str = field(default_factory=lambda: os.getenv("MCP_URL", "http://localhost:8100/mcp"))
    pipeline_timeout_s: int = field(default_factory=lambda: int(os.getenv("PIPELINE_TIMEOUT_S", "900")))
    max_concurrent_runs: int = field(default_factory=lambda: int(os.getenv("MAX_CONCURRENT_RUNS", "4")))
    # Reviewer: how many times a failing pack is sent back to the orchestrator, and how many of a policy excerpt's
    # words must appear in the section it cites.
    review_max_sendbacks: int = field(default_factory=lambda: int(os.getenv("REVIEW_MAX_SENDBACKS", "1")))
    review_quote_overlap: float = field(default_factory=lambda: float(os.getenv("REVIEW_QUOTE_OVERLAP", "0.8")))
    # The reviewer's LLM stage: on/off, which model, and how long it may take before the code stage's verdict stands.
    review_llm_enabled: bool = field(default_factory=lambda: os.getenv("REVIEW_LLM", "1") != "0")
    reviewer_model: str = field(default_factory=lambda: os.getenv("REVIEWER_MODEL") or os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5"))
    reviewer_timeout_s: int = field(default_factory=lambda: int(os.getenv("REVIEWER_TIMEOUT_S", "180")))
    # Off by default: the LLM stage only warns. On, its findings can block (and send the pack back) under the rules in
    # reviewer_llm.apply_llm_review.
    review_llm_blocks: bool = field(default_factory=lambda: os.getenv("REVIEW_LLM_BLOCKS", "0") == "1")
    # Available to your pipeline. Never hard-code keys: they come from .env.
    anthropic_api_key: str | None = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY") or None)
    claude_model: str = field(default_factory=lambda: os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5"))
    tavily_api_key: str | None = field(default_factory=lambda: os.getenv("TAVILY_API_KEY") or None)
    langfuse_host: str | None = field(default_factory=lambda: os.getenv("LANGFUSE_HOST") or None)
    langfuse_public_key: str | None = field(default_factory=lambda: os.getenv("LANGFUSE_PUBLIC_KEY") or None)
    langfuse_secret_key: str | None = field(default_factory=lambda: os.getenv("LANGFUSE_SECRET_KEY") or None)
    ollama_url: str = field(default_factory=lambda: os.getenv("OLLAMA_URL", "http://localhost:11434"))
    data_dir: str = field(default_factory=lambda: os.getenv("DATA_DIR", "/data"))


settings = Settings()
