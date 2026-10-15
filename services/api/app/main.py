"""
KYC API service.

  POST /runs                    start a run for a company and country
  GET  /runs                    list runs
  GET  /runs/{id}               status, pack, events and reviews
  GET  /runs/{id}/stream        live progress (server-sent events)
  POST /runs/{id}/review        approve, decline or send back with comments
  GET  /schema                  JSON schema of the KYC pack
  GET  /companies               the test set of company names (for the web form)
  GET  /health                  health of the API, database and mock data API

Interactive docs: http://localhost:8000/docs
"""
from __future__ import annotations

from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from kyc_common.schema import json_schema
from pydantic import BaseModel, Field

from . import db, runs
from .settings import settings

app = FastAPI(title="KYC onboarding API", version="1.0.0",
              description="Submit a company, follow progress, review the KYC onboarding pack.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class RunRequest(BaseModel):
    company_name: str = Field(min_length=2, max_length=200, examples=["Tejo Engenharia"])
    country: Literal["UK", "PT", "BR"]


class ReviewRequest(BaseModel):
    decision: Literal["approve", "decline", "send_back"]
    comments: str | None = Field(default=None, max_length=4000)
    reviewer: str | None = Field(default=None, max_length=200)


@app.get("/health", tags=["meta"])
async def health():
    db_ok = await db.ping()
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            mock_ok = (await c.get(f"{settings.mockdata_url}/health")).status_code == 200
    except httpx.HTTPError:
        mock_ok = False
    return {"status": "ok" if db_ok and mock_ok else "degraded", "database": db_ok, "mockdata": mock_ok,
            "anthropic_key_configured": bool(settings.anthropic_api_key)}


@app.get("/schema", tags=["meta"], summary="JSON schema of the KYC onboarding pack")
def schema():
    return json_schema()


@app.get("/companies", tags=["meta"], summary="Company names in the test set (for suggestions in the web app)")
async def companies(country: str | None = None):
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get(f"{settings.mockdata_url}/meta/companies", params={"country": country} if country else None)
        r.raise_for_status()
        return r.json()


@app.post("/runs", status_code=201, tags=["runs"])
async def create_run(req: RunRequest):
    run = await db.create_run(req.company_name.strip(), req.country)
    runs.start(run["id"], run["company_name"], run["country"])
    return {"id": run["id"], "status": run["status"]}


@app.get("/runs", tags=["runs"])
async def list_runs(limit: int = Query(50, ge=1, le=500), company_name: str | None = None, country: str | None = None):
    return {"items": await db.list_runs(limit, company_name, country)}


@app.get("/runs/{run_id}", tags=["runs"])
async def get_run(run_id: str):
    run = await db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@app.get("/runs/{run_id}/stream", tags=["runs"], summary="Server-sent events with live progress")
async def stream(run_id: str):
    if not await db.get_run(run_id):
        raise HTTPException(404, "Run not found")
    return StreamingResponse(runs.stream(run_id), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/runs/{run_id}/review", tags=["runs"])
async def review(run_id: str, req: ReviewRequest):
    try:
        return await runs.review(run_id, req.decision, req.comments, req.reviewer)
    except LookupError:
        raise HTTPException(404, "Run not found")
    except ValueError as e:
        raise HTTPException(409, str(e))
