"""Runs the pipeline in the background, records progress and handles human reviews."""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from kyc_common import KycPack, Review, sourcing_problems
from pydantic import ValidationError

from . import db
from .context import PipelineContext
from .pipeline import run_pipeline
from .settings import settings

TERMINAL_FOR_STREAM = {"awaiting_review", "approved", "declined", "failed"}
_subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
_tasks: set[asyncio.Task] = set()
_slots = asyncio.Semaphore(settings.max_concurrent_runs)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def publish(run_id: str, event: dict[str, Any]) -> None:
    event = {"ts": _now(), **event}
    await db.append_event(run_id, event)
    for q in list(_subscribers.get(run_id, [])):
        q.put_nowait(event)


async def set_status(run_id: str, status: str, **extra: Any) -> None:
    await db.update(run_id, status=status, **extra)
    await publish(run_id, {"type": "status", "status": status})


def start(run_id: str, company_name: str, country: str, version: int = 1,
          feedback: str | None = None, previous_pack: dict | None = None) -> None:
    task = asyncio.create_task(_execute(run_id, company_name, country, version, feedback, previous_pack))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _execute(run_id: str, company_name: str, country: str, version: int,
                   feedback: str | None, previous_pack: dict | None) -> None:
    async with _slots:
        await set_status(run_id, "running", version=version)
        ctx = PipelineContext(
            run_id=run_id, company_name=company_name, country=country, version=version, feedback=feedback,
            previous_pack=KycPack.model_validate(previous_pack) if previous_pack else None,
            _emit=lambda e: publish(run_id, e))
        try:
            result = await asyncio.wait_for(run_pipeline(ctx), timeout=settings.pipeline_timeout_s)
            pack = result if isinstance(result, KycPack) else KycPack.model_validate(result)
            pack.review = Review(status="pending")
            problems = sourcing_problems(pack)
            if problems:
                await publish(run_id, {"type": "warning", "step": "validation",
                                       "message": f"{len(problems)} fact(s) break 'no source, no claim'",
                                       "data": {"problems": problems[:25]}})
            run = await db.get_run(run_id)
            history = run["pack_history"] or []
            if run["pack"]:
                history.append(run["pack"])
            await db.update(run_id, pack=json.loads(pack.model_dump_json()), pack_history=history,
                            review_status="pending", error=None)
            await set_status(run_id, "awaiting_review")
            await publish(run_id, {"type": "done", "message": "Pack ready for review"})
        except asyncio.TimeoutError:
            await _fail(run_id, f"Pipeline timed out after {settings.pipeline_timeout_s}s")
        except ValidationError as e:
            await _fail(run_id, f"Pipeline returned an invalid pack: {e.error_count()} schema error(s). First: {e.errors()[0]['loc']} {e.errors()[0]['msg']}")
        except Exception as e:  # noqa: BLE001 - surface any pipeline error to the user
            await _fail(run_id, f"{type(e).__name__}: {e}")


async def _fail(run_id: str, message: str) -> None:
    await set_status(run_id, "failed", error=message)
    await publish(run_id, {"type": "error", "message": message})
    await publish(run_id, {"type": "done", "message": "Run failed"})


async def review(run_id: str, decision: str, comments: str | None, reviewer: str | None) -> dict:
    run = await db.get_run(run_id)
    if not run:
        raise LookupError("run not found")
    if run["status"] != "awaiting_review":
        raise ValueError(f"Run is '{run['status']}', not awaiting review")
    if decision == "send_back" and not (comments or "").strip():
        raise ValueError("Comments are required when sending a pack back")
    now = datetime.now(timezone.utc)
    status = {"approve": "approved", "decline": "declined", "send_back": "sent_back"}[decision]
    pack = run["pack"]
    pack["review"] = Review(status=status, comments=comments, reviewer=reviewer, reviewed_at=now).model_dump(mode="json")
    reviews = (run["reviews"] or []) + [{"version": run["version"], "decision": status, "comments": comments,
                                          "reviewer": reviewer, "at": now.isoformat()}]
    await db.update(run_id, pack=pack, review_status=status, reviews=reviews)
    await publish(run_id, {"type": "review", "message": f"Pack v{run['version']} {status.replace('_', ' ')}",
                           "data": {"decision": status, "comments": comments}})
    if decision == "send_back":
        await set_status(run_id, "sent_back")
        start(run_id, run["company_name"], run["country"], version=run["version"] + 1, feedback=comments, previous_pack=pack)
    else:
        await set_status(run_id, status)
    return await db.get_run(run_id)


async def stream(run_id: str) -> AsyncIterator[str]:
    """Server-sent events: past events first, then live ones until the run settles."""
    q: asyncio.Queue = asyncio.Queue()
    _subscribers[run_id].append(q)
    try:
        run = await db.get_run(run_id)
        for e in run["events"] or []:
            yield f"data: {json.dumps(e, default=str)}\n\n"
        if run["status"] in TERMINAL_FOR_STREAM:
            yield f"data: {json.dumps({'type': 'end', 'status': run['status']})}\n\n"
            return
        while True:
            try:
                e = await asyncio.wait_for(q.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            yield f"data: {json.dumps(e, default=str)}\n\n"
            if e.get("type") == "done":
                current = await db.get_run(run_id)
                yield f"data: {json.dumps({'type': 'end', 'status': current['status']})}\n\n"
                return
    finally:
        _subscribers[run_id].remove(q)
