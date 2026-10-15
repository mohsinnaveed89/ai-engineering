import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "postgresql://academy:academy@localhost:5432/academy"))
    mockdata_url: str = field(default_factory=lambda: os.getenv("MOCKDATA_URL", "http://localhost:8001"))
    mcp_url: str = field(default_factory=lambda: os.getenv("MCP_URL", "http://localhost:8100/mcp"))
    pipeline_timeout_s: int = field(default_factory=lambda: int(os.getenv("PIPELINE_TIMEOUT_S", "900")))
    max_concurrent_runs: int = field(default_factory=lambda: int(os.getenv("MAX_CONCURRENT_RUNS", "4")))
    # Available to your pipeline. Never hard-code keys: they come from .env.
    anthropic_api_key: str | None = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY") or None)
    claude_model: str = field(default_factory=lambda: os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5"))
    tavily_api_key: str | None = field(default_factory=lambda: os.getenv("TAVILY_API_KEY") or None)
    langfuse_host: str | None = field(default_factory=lambda: os.getenv("LANGFUSE_HOST") or None)


settings = Settings()
