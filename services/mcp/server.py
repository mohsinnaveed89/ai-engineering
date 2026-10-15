"""
MCP server skeleton for the KYC data.

It exposes ONE working tool, `uk_search_companies`, that calls the mock data
API. Your job (Day 3): extend it so that ALL data the agents use goes through
MCP: the three registers, news, bank systems and document search.

Run locally:      python server.py            (Streamable HTTP on http://localhost:8100/mcp)
Inspect it:       npx @modelcontextprotocol/inspector   then connect to http://localhost:8100/mcp
From the Agent SDK (inside docker compose the URL is http://mcp:8100/mcp):
    options = ClaudeAgentOptions(mcp_servers={"kyc": {"type": "http", "url": settings.mcp_url}})

Tips from 'Writing effective tools for agents':
  * a few tools that match real tasks beat one tool per endpoint
  * namespace tool names (uk_..., pt_..., br_..., news_..., bank_...)
  * return concise, high-signal fields and helpful, actionable errors
"""
from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

MOCKDATA_URL = os.getenv("MOCKDATA_URL", "http://localhost:8001").rstrip("/")

mcp = FastMCP(
    "kyc-data",
    instructions="Tools for KYC research on companies in the UK, Portugal and Brazil. All data is fictional.",
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT", "8100")),
    # Local development inside docker compose: accept the service hostname as well as localhost.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


@mcp.tool()
async def uk_search_companies(name: str, limit: int = 5) -> dict:
    """Search the UK company register by company name.

    Use this first to find a UK company's 8-character company number, then look up
    its profile, officers and owners with the number.

    Args:
        name: Full or partial company name, e.g. "Hawksmoor Meridian Textiles".
        limit: Maximum number of matches to return (1 to 20).

    Returns matches with company_number, title (registered name), company_status,
    date_of_creation and address_snippet. An empty list means no plausible match.
    """
    async with httpx.AsyncClient(base_url=MOCKDATA_URL, timeout=15) as client:
        r = await client.get("/uk/search", params={"q": name, "limit": max(1, min(limit, 20))})
        if r.status_code >= 500:
            return {"error": "The UK register is temporarily unavailable. Try again in a few seconds."}
        r.raise_for_status()
        data = r.json()
    return {"matches": data["items"], "source": f"{MOCKDATA_URL}/uk/search?q={name}"}


# TODO (Day 3): add tools for the UK profile, officers and PSCs, the PT register, the BR CNPJ
# lookup, news search, bank systems and document search. Add resources (e.g. the KYC policy)
# and prompts (e.g. a pack review template) too.


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
