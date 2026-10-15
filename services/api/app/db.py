"""Run storage in Postgres (table `runs`, created by db/init.sql)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .settings import settings

COLUMNS = "id, company_name, country, status, version, pack, pack_history, events, review_status, reviews, error, created_at, updated_at"


async def connect() -> psycopg.AsyncConnection:
    return await psycopg.AsyncConnection.connect(settings.database_url, row_factory=dict_row, autocommit=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def ping() -> bool:
    try:
        async with await connect() as conn:
            await conn.execute("SELECT 1")
        return True
    except Exception:
        return False


async def create_run(company_name: str, country: str) -> dict:
    rid = uuid.uuid4()
    async with await connect() as conn:
        cur = await conn.execute(
            f"INSERT INTO runs (id, company_name, country, status) VALUES (%s, %s, %s, 'queued') RETURNING {COLUMNS}",
            (rid, company_name, country))
        return _row(await cur.fetchone())


async def get_run(run_id: str) -> dict | None:
    async with await connect() as conn:
        cur = await conn.execute(f"SELECT {COLUMNS} FROM runs WHERE id = %s", (run_id,))
        row = await cur.fetchone()
        return _row(row) if row else None


async def list_runs(limit: int = 50, company_name: str | None = None, country: str | None = None) -> list[dict]:
    q = "SELECT id, company_name, country, status, version, review_status, created_at, updated_at, " \
        "pack->'risk'->>'rating' AS risk_rating, pack->'risk'->>'recommendation' AS recommendation FROM runs"
    where, params = [], []
    if company_name:
        where.append("company_name = %s"); params.append(company_name)
    if country:
        where.append("country = %s"); params.append(country)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)
    async with await connect() as conn:
        cur = await conn.execute(q, params)
        return [_row(r) for r in await cur.fetchall()]


async def update(run_id: str, **fields: Any) -> None:
    sets, params = [], []
    for k, v in fields.items():
        sets.append(f"{k} = %s")
        params.append(json.dumps(v, default=str) if k in ("pack", "pack_history", "reviews") and v is not None else v)
    sets.append("updated_at = %s")
    params.append(_now())
    params.append(run_id)
    async with await connect() as conn:
        await conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE id = %s", params)


async def append_event(run_id: str, event: dict) -> None:
    async with await connect() as conn:
        await conn.execute("UPDATE runs SET events = events || %s::jsonb, updated_at = %s WHERE id = %s",
                           (json.dumps([event], default=str), _now(), run_id))


def _row(r: dict) -> dict:
    r = dict(r)
    r["id"] = str(r["id"])
    return r
