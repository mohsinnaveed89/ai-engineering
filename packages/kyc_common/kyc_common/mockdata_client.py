"""Thin async client for the mock data API, with retries. Use it, extend it, or replace it with MCP."""
from __future__ import annotations

import asyncio
from typing import Any

import httpx


class MockDataClient:
    def __init__(self, base_url: str, timeout: float = 20.0, retries: int = 3):
        self.base_url = base_url.rstrip("/")
        self.retries = retries
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def __aenter__(self) -> "MockDataClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    def url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    async def get(self, path: str, **params: Any) -> Any | None:
        """GET a path. Returns parsed JSON, or None on 404. Retries 5xx and network errors."""
        params = {k: v for k, v in params.items() if v is not None}
        for attempt in range(self.retries):
            try:
                r = await self._client.get(path, params=params)
                if r.status_code == 404:
                    return None
                if r.status_code >= 500:
                    raise httpx.HTTPStatusError("server error", request=r.request, response=r)
                r.raise_for_status()
                return r.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500:
                    raise
                if attempt == self.retries - 1:
                    raise
                await asyncio.sleep(0.5 * 2 ** attempt)
        return None
