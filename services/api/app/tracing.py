"""
Tracing helpers for Day 6 (observability bonus). A starting point, not the finished job.

Two sources of spans end up in the same Langfuse project (start it with `docker compose --profile tracing up`):

1. The Claude Agent SDK. It runs the Claude Code CLI, which has OpenTelemetry built in and can export
   spans for each model request, tool call and subagent. Pass `agent_sdk_otel_env()` as `env=` in
   `ClaudeAgentOptions` to send them to Langfuse. By default these spans are structural (names,
   durations, tokens), not prompt or tool content. Content is only added if you set OTEL_LOG_* variables:
   don't, unless your traces are masked.

2. Your own code. Use `langfuse_client()` to create spans around your pipeline, agents and tools
   (see the Langfuse Python SDK docs). Everything you send through it passes `mask_pii()` first.

Your job: make every agent step traceable AND make sure no personal data (names, dates of birth,
addresses, ID numbers) leaves the app. Start by implementing `mask_pii()`.
"""
from __future__ import annotations

import base64
from typing import Any

from .settings import settings


def langfuse_configured() -> bool:
    return bool(settings.langfuse_host and settings.langfuse_public_key and settings.langfuse_secret_key)


def agent_sdk_otel_env(service_name: str = "kyc-agents", **resource_attributes: str) -> dict[str, str]:
    """Environment for ClaudeAgentOptions(env=...) that exports Agent SDK traces to Langfuse.

    Returns an empty dict when Langfuse isn't configured, so it is always safe to pass.
    Traces from the Agent SDK are in beta: span names may change between versions.
    """
    if not langfuse_configured():
        return {}
    auth = base64.b64encode(f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}".encode()).decode()
    env = {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_METRICS_EXPORTER": "none",   # Langfuse ingests traces
        "OTEL_LOGS_EXPORTER": "none",
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": f"{settings.langfuse_host.rstrip('/')}/api/public/otel/v1/traces",
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS": f"Authorization=Basic {auth},x-langfuse-ingestion-version=4",
        "OTEL_TRACES_EXPORT_INTERVAL": "1000",
        "OTEL_SERVICE_NAME": service_name,
    }
    if resource_attributes:
        env["OTEL_RESOURCE_ATTRIBUTES"] = ",".join(f"{k}={v}" for k, v in resource_attributes.items())
    return env


def mask_pii(data: Any, **kwargs: Any) -> Any:
    """Called by the Langfuse client on every input, output and metadata value before it is sent.

    TODO (Day 6): replace personal data with placeholders, e.g. "[PERSON]", "[DOB]", "[ADDRESS]",
    "[ID]". Recurse through dicts and lists. Test it: no name from the registers should appear in Langfuse.
    """
    return data


def langfuse_client():
    """A Langfuse client with mask_pii applied, or None if Langfuse isn't configured."""
    if not langfuse_configured():
        return None
    from langfuse import Langfuse

    return Langfuse(public_key=settings.langfuse_public_key, secret_key=settings.langfuse_secret_key,
                    host=settings.langfuse_host, mask=mask_pii)
