# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Starter kit for the Indicium AI Global AI Academy challenge ("Due Diligence, World Edition"). An analyst submits a company (name + country `UK`/`PT`/`BR`); a pipeline builds a KYC onboarding pack from mock data; a human approves, declines or sends it back. The kit works end to end with a no-AI placeholder pipeline. The task is to replace it with Claude Agent SDK agents. All companies and people are fictional.

## Commands

```bash
cp .env.example .env                       # ANTHROPIC_API_KEY not needed for the placeholder
docker compose up --build                  # web :3000, api :8000, mockdata :8001, mcp :8100, postgres :5432
docker compose --profile tracing up        # + Langfuse :3001 (~4 GB RAM)
docker compose --profile embeddings up -d ollama   # local embeddings (bge-m3)

# Run + score one company (fastest loop; no web app/DB)
docker compose exec api python -m app.cli "Hawksmoor Meridian Textiles" UK
docker compose exec api python -m app.cli --company-id BR-012 --out /tmp/pack.json
docker compose exec api python -m app.cli --company-id BR-012 --previous /tmp/pack.json --feedback "Check the UBOs"   # simulate send-back

# Scoring (reports land in scoring/reports/)
docker compose run --rm scoring --run-all --label my-change
docker compose run --rm scoring --run-all --country UK --difficulty straightforward
docker compose run --rm scoring --latest
docker compose run --rm scoring --run-all --label v2 --compare scoring/reports/report-<ts>-<label>.json

# Tests (scoring sanity checks: correct answers must score 100%)
python -m venv .venv && source .venv/bin/activate
pip install -e packages/kyc_common -r services/api/requirements.txt -r services/mockdata/requirements.txt pytest
pytest tests
pytest tests/test_scoring.py -k <name>     # single test
```

Hot reload: `services/api/app` is mounted into the api container and reloads on save. After editing `services/mcp/server.py` run `docker compose restart mcp`. New Python deps go in `services/api/requirements.txt`, then `docker compose up --build api`. `db/init.sql` only runs on an empty volume (`docker compose down -v` to reset).

## Architecture

Web app (`services/web`, static, no build) → API (`services/api`, FastAPI, runs stored in Postgres, SSE progress) → `run_pipeline(ctx)` → data via the mock data API (`services/mockdata`, offline UK/PT/BR registers, news, bank systems, documents) or, from Day 3, via the MCP server (`services/mcp/server.py`, FastMCP over Streamable HTTP, which wraps the mock data API).

- **Single entry point:** `services/api/app/pipeline.py::run_pipeline(ctx: PipelineContext) -> KycPack`. Currently delegates to `placeholder.py`. `ctx` (see `app/context.py`) carries company/country, `version`, `feedback`, `previous_pack`, `review_history`, `emit()` for live progress, `record_metrics()` for token/cost/latency, and `settings` (URLs and keys from `.env`).
- **Shared schema:** `packages/kyc_common/kyc_common/schema.py` (Pydantic) is the fixed KYC pack contract used by the API, web app and scoring tool. **Do not change it** (`docs/pack.schema.json` mirrors it). `kyc_common.sourcing_problems(pack)` lists violations of "no source, no claim": every fact must cite source ids that exist in `pack.sources`.
- **Run lifecycle:** `queued → running → awaiting_review → approved/declined`, or `sent_back → running` (new version with feedback), or `failed`. A human always makes the final decision. Pipelines time out after `PIPELINE_TIMEOUT_S`.
- **Scoring:** `scoring/score.py` compares packs against `data/generated/answers.json` per field (identity, directors/UBO/news F1, financials within 1%, risk vs. KYC policy, flags). It also reports latency and cost.
- **Data:** `data/generated/` (150 companies, committed, produced by `data/generator/generate.py`) with built-in challenge cases (holding chains, offshore owners, conflicting bank data, namesakes, name-collision news, inactive, prohibited sectors). `data/documents/` holds annual-report extracts (UK English, PT/BR Portuguese; some in thousands, multi-year, consolidated) and `policies/` (KYC policy with risk-point rules, country procedures suited to Agent Skills). BR register is lookup by CNPJ only.
- **Agent SDK conventions:** use `services/api/app/agent_home/` as the agent `cwd` (Skills in `.claude/skills/` load with `skills="all"`); prompts go in `services/api/app/prompts/`; suggested module homes are `app/agents/`, `app/tools/`, `app/rag/`. The API container runs non-root (uid 1000) because the Claude Code CLI refuses `bypassPermissions` as root; write only to `/tmp` or home (`/data` is read-only). Give agents least privilege via `allowed_tools`. Tracing helpers are in `app/tracing.py` (`mask_pii()` is left for you to implement; mask before anything leaves the app).

## Rules from the challenge

- The three pack rules apply to every prompt and every pack: **no source, no claim** (every fact cites source ids that exist in `pack.sources`); **flag, don't judge** (concerns go in `flags`, in neutral wording); **the AI recommends, the human decides** (`risk.recommendation` is advice, `review` stays `pending`). The system prompt is `services/api/app/prompts/orchestrator.md`.
- Do not change `packages/kyc_common/kyc_common/schema.py` or `docs/pack.schema.json`.
- Baseline to beat: `scoring/reports/report-20261019-161453-baseline.{md,json}` (placeholder, 66.0% overall). Compare changes with `--compare` against it; reports are git-ignored, so don't delete them.
- Log each prompt change in `services/api/app/prompts/CHANGELOG.md` (change, hypothesis, before score, after score).
- Keep secrets in `.env` (git-ignored).
- Never read `data/generated/answers.json` or the generator internals from the pipeline (it is the exam paper; only the scoring tool uses it).
- `MOCK_LATENCY_MS` / `MOCK_ERROR_RATE` in `.env` simulate slow or flaky (503) data sources.
