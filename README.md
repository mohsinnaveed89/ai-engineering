# AI Academy starter kit: Due Diligence, World Edition

Starter kit for the Indicium AI **Global AI Academy** challenge. It runs end to end **without any AI**: you submit a company, a placeholder pipeline builds a KYC onboarding pack from mock data, and a human approves, declines or sends it back. Your job is to replace the placeholder with a team of AI agents built on the **Claude Agent SDK**.

> Academy guide (syllabus, daily builds, scoring planner): https://sites.indicium.ai/ai-engineering-academy/
>
> All companies and people in this repository are fictional. Any resemblance to a real company or person is a coincidence.

- [Quick start](#quick-start)
- [Day by day: what to use when](#day-by-day-what-to-use-when)
- [What's in the box](#whats-in-the-box)
- [Where to write your code](#where-to-write-your-code)
- [The KYC pack (output schema)](#the-kyc-pack-output-schema)
- [API reference](#api-reference)
- [Mock data API](#mock-data-api)
- [MCP server](#mcp-server)
- [Documents for RAG](#documents-for-rag)
- [Tracing with Langfuse](#tracing-with-langfuse)
- [Scoring](#scoring)
- [The generated data](#the-generated-data)
- [Rules](#rules)
- [Troubleshooting](#troubleshooting)

---

## Quick start

You need Docker Desktop 4.27 or later (Docker Compose 2.24+). Python 3.12 is optional for running tools outside Docker.

```bash
git clone git@github.com:MeshAI/ai-academy-starter-kit.git
cd ai-academy-starter-kit
cp .env.example .env          # add your ANTHROPIC_API_KEY (not needed for the placeholder)
docker compose up --build
```

Then open:

| What | URL |
|---|---|
| Web app | http://localhost:3000 |
| API docs (Swagger) | http://localhost:8000/docs |
| Mock data API docs | http://localhost:8001/docs |
| MCP server (Streamable HTTP) | http://localhost:8100/mcp |
| Langfuse (with `--profile tracing`) | http://localhost:3001 (login `academy@example.com` / `academy-local`) |

Submit a company in the web app (start typing to see the test set), watch the progress, then approve, decline or send the pack back with comments. Then get a baseline score:

```bash
docker compose run --rm scoring --run-all --label baseline
```

### Definition of done for the kit

On a clean machine, `docker compose up` starts everything and you can:

- submit a company from the web app and receive a valid placeholder pack
- approve, decline or send back a pack
- run the scoring tool across the full company set

---

## Day by day: what to use when

| Day | Theme | What you build | Kit pieces to use |
|---|---|---|---|
| 1 | Set up and talk to the model | Kit running; one Claude call that fills in company details with a source per field | `docker compose up`, `services/api/app/pipeline.py`, `app/mockdata_client.py`, `app/prompts/`, `python -m app.cli`, `scoring --country UK --difficulty straightforward` |
| 2 | Give it hands | One Claude Agent SDK agent that builds the whole pack (happy path) | Mock data API as custom tools, `ctx.emit()`, `kyc_common.sourcing_problems()`, the web app's approve/decline |
| 3 | Plug into the data | All data through MCP; live news in the local language | `services/mcp/server.py`, MCP Inspector, `ctx.settings.mcp_url`, `TAVILY_API_KEY` (optional) |
| 4 | Read the fine print | Cited financials and policy-aware risk; country procedures as Skills | `data/documents/`, Postgres + pgvector, `--profile embeddings` (Ollama), `app/agent_home/.claude/skills/` |
| 5 | Assemble the team | Specialist subagents in parallel, a reviewer loop, send back with comments | `ctx.feedback`, `ctx.previous_pack`, `ctx.review_history`, `python -m app.cli --feedback ... --previous ...` |
| 6 | Prove it | Traces with personal data masked; full eval; a measured prompt improvement | `--profile tracing`, `app/tracing.py`, `ctx.record_metrics()`, `scoring --run-all --label ... --compare ...` |
| 7 | Make it work | The capstone: everything from one `docker compose up`, plus your own extensions | Everything above; Dockerfiles for cloud deployment |

---

## What's in the box

```
┌──────────┐     ┌───────────┐     ┌──────────────────────────────┐
│ Web app  │────▶│ API       │────▶│ pipeline.py  ◀── YOUR AGENTS │
│ :3000    │ /api│ :8000     │     └──────────────┬───────────────┘
└──────────┘     │ runs in   │                    │ HTTP today, MCP from Day 3
                 │ Postgres  │     ┌──────────────▼───────────────┐
                 └─────┬─────┘     │ MCP server :8100 (skeleton)  │
                       │           └──────────────┬───────────────┘
                 ┌─────▼─────┐     ┌──────────────▼───────────────┐
                 │ Postgres  │     │ Mock data API :8001          │
                 │ + pgvector│     │ registers · news · bank ·    │
                 │ :5432     │     │ documents (all offline)      │
                 └───────────┘     └──────────────────────────────┘
  Langfuse :3001 (tracing profile)     Scoring tool (tools profile)
```

| Component | Path | What it does |
|---|---|---|
| Web app | `services/web` | Submit a company, follow progress live, review the pack with sources linked to each fact; approve, decline, send back with comments. No build step. |
| API service | `services/api` | Starts runs, stores them in Postgres, streams progress (SSE), handles reviews. Calls `run_pipeline()`. |
| Placeholder pipeline | `services/api/app/placeholder.py` | Builds a valid pack from the mock data with no AI. Your baseline. |
| Shared schema | `packages/kyc_common` | The fixed KYC pack schema (Pydantic) and helpers used by the API and the scoring tool. |
| Mock data API | `services/mockdata` | UK, Portugal and Brazil registers, news (English and Portuguese), internal bank systems, documents. |
| Company data generator | `data/generator` | Generates the fictional companies, the documents and the correct answers. |
| Documents for RAG | `data/documents` | Annual-report extracts (English and Portuguese) and a sample KYC policy with country procedures. |
| MCP server skeleton | `services/mcp` | One working tool calling the mock data API, ready to extend. |
| Database | `db/init.sql` | Postgres 17 with pgvector enabled and a `runs` table. |
| Scoring tool | `scoring/score.py` | Checks schema and sourcing, compares packs with the correct answers, reports per field and per company. |
| Tracing | `docker-compose.yml` (`tracing` profile), `services/api/app/tracing.py` | Self-hosted Langfuse with a pre-created project and API keys, plus helpers to connect the Agent SDK. |
| One-company runner | `services/api/app/cli.py` | Runs your pipeline for one company in the terminal and scores it. |
| Embeddings (optional) | `docker-compose.yml` (`embeddings` profile) | Ollama for local, offline multilingual embeddings. |

---

## Where to write your code

**One entry point: `services/api/app/pipeline.py`.**

```python
async def run_pipeline(ctx: PipelineContext) -> KycPack:
    # TODO: replace the placeholder with your own pipeline.
    return await build_placeholder_pack(ctx)
```

`ctx` gives you:

| Field | Meaning |
|---|---|
| `ctx.company_name`, `ctx.country` | What the analyst submitted (`UK`, `PT` or `BR`) |
| `ctx.version` | 1 for the first pack, +1 each time the human sends it back |
| `ctx.feedback`, `ctx.previous_pack` | The reviewer's comments and the pack they sent back |
| `ctx.review_history` | Every earlier human decision on this run |
| `await ctx.emit(step, message)` | Live progress in the web app |
| `await ctx.record_metrics(input_tokens=..., output_tokens=..., cost_usd=..., model=...)` | Usage for the run; numbers add up across calls. Shown with latency in the scoring report |
| `ctx.settings` | URLs (`mockdata_url`, `mcp_url`, `database_url`, `ollama_url`) and keys from `.env` |

The API validates what you return against the schema, checks that every fact has a source (problems appear as a warning in the web app), stores the pack and asks a human to review it.

`services/api/app` is mounted into the container and the server reloads on save, so you don't need to rebuild while you work. Add Python dependencies to `services/api/requirements.txt` and run `docker compose up --build api`. The `anthropic`, `claude-agent-sdk`, `mcp` and `langfuse` packages are already installed.

Good places for your own modules: `services/api/app/agents/`, `services/api/app/tools/`, `services/api/app/rag/`. Put prompts in `services/api/app/prompts/` (see its README) so you can version and tune them.

### Run one company from the terminal

The fastest loop while you iterate: no web app, no database, a score in seconds.

```bash
docker compose exec api python -m app.cli "Hawksmoor Meridian Textiles" UK
docker compose exec api python -m app.cli --company-id BR-012 --out /tmp/pack.json
# simulate a human sending it back (Day 5)
docker compose exec api python -m app.cli --company-id BR-012 --previous /tmp/pack.json --feedback "Check the UBOs"
```

It prints progress, sourcing problems and a field-by-field score when the company is in the test set.

### Claude Agent SDK notes

- The API container runs as a non-root user, because the Claude Code CLI under the SDK refuses `permission_mode="bypassPermissions"` as root.
- Give agents least privilege: list your own tools in `allowed_tools` and don't hand them `Bash` or `Write` unless you mean to.
- Use `services/api/app/agent_home/` as the agent's `cwd`: Skills in its `.claude/skills/` folder load with `skills="all"` (see `agent_home/README.md`).

---

## The KYC pack (output schema)

Defined in `packages/kyc_common/kyc_common/schema.py`; JSON schema in `docs/pack.schema.json` and at `GET /schema`. **Don't change it**: the web app and the scoring tool depend on it.

| Section | Contents |
|---|---|
| `company` | `legal_name`, `registration_number`, `status`, `address`, `incorporation_date`, `sector`, each `{value, sources}` |
| `officers` | Directors and other officers: `name`, `role`, `appointed_on`, `sources` |
| `owners` | `name`, `type` (person/company), `ownership_pct` (effective), `is_ultimate_beneficial_owner`, `jurisdiction`, `via` (chain), `sources` |
| `news` | `title`, `url`, `published_at`, `language`, `summary`, `sentiment` (positive/neutral/adverse), `severity` (none/medium/high), `sources` |
| `financial_summary` | `year`, `currency`, `revenue`, `net_profit`, `total_assets`, `going_concern_uncertainty`, `summary`, `sources` |
| `risk` | `rating` (low/medium/high), `recommendation` (approve/decline/investigate_further), `rationale`, `factors` |
| `flags` | `type`, `severity`, `message`, `sources`. Types: `conflicting_data`, `missing_data`, `complex_ownership`, `high_risk_jurisdiction`, `ubo_unidentified`, `adverse_media`, `high_risk_sector`, `prohibited_sector`, `inactive_company`, `other` |
| `sources` | `id`, `type` (registry/news/bank_system/document/web/other), `title`, `reference` (URL, API path or doc id), `excerpt`, `retrieved_at` |
| `review` | Set by the API from the human decision |

**No source, no claim:** every fact lists source ids that exist in `sources`. `kyc_common.sourcing_problems(pack)` returns every violation; use it in your reviewer agent.

---

## API reference

Interactive docs at http://localhost:8000/docs.

| Method | Path | Body / params | Returns |
|---|---|---|---|
| `POST` | `/runs` | `{"company_name": "...", "country": "UK"}` | `{id, status}` |
| `GET` | `/runs` | `limit`, `company_name`, `country` | Run summaries, newest first |
| `GET` | `/runs/{id}` | | Status, version, pack, pack history, events, reviews, error |
| `GET` | `/runs/{id}/stream` | | Server-sent events: progress, status, warnings, `done`, `end` |
| `POST` | `/runs/{id}/review` | `{"decision": "approve" \| "decline" \| "send_back", "comments": "...", "reviewer": "..."}` | Updated run. `send_back` requires comments and starts a new version with the feedback |
| `GET` | `/schema` | | JSON schema of the pack |
| `GET` | `/companies` | `country` | Company names in the test set |
| `GET` | `/health` | | API, database and mock data status |

Run statuses: `queued` → `running` → `awaiting_review` → `approved` / `declined`, or `sent_back` → `running` (new version), or `failed`.

---

## Mock data API

Interactive docs at http://localhost:8001/docs. Every endpoint is offline. Responses use each country's own shape and language, as the real sources would.

| Area | Endpoints | Notes |
|---|---|---|
| UK register | `GET /uk/search?q=`, `/uk/company/{number}`, `/uk/company/{number}/officers`, `/uk/company/{number}/persons-with-significant-control` | Companies House style. This mock lists every shareholder in PSC with an exact `ownership_percentage`. |
| PT register | `GET /pt/pesquisa?nome=`, `/pt/entidade/{nipc}` | Portuguese field names: `denominacao`, `estado`, `sede`, `orgaos_sociais`, `socios`. |
| BR register | `GET /br/cnpj/{cnpj}` | **Lookup by CNPJ only, no name search**, as in reality. Find the CNPJ first (the bank's onboarding request, news...). |
| News | `GET /news/search?q=&language=&country=`, `/news/{id}` | English and Portuguese. Some articles are about other companies with similar names. |
| Bank systems | `GET /bank/prospects?name=&country=`, `/bank/prospects/{id}`, `/bank/relationships/{registration_number}`, `/bank/risk-appetite` | Onboarding requests hold **declared, unverified** data, which can differ from the register. |
| Documents | `GET /documents?type=&country=&registration_number=&company=`, `/documents/{doc_id}` | Annual-report extracts and policies. |

Set `MOCK_LATENCY_MS` and `MOCK_ERROR_RATE` in `.env` to make the APIs slower or flaky (503s) and test how your agents cope.

---

## MCP server

`services/mcp/server.py` is a FastMCP server over Streamable HTTP with one working tool, `uk_search_companies`. Day 3 is about moving **all** data access behind it (bonus: *MCP*).

```bash
npx @modelcontextprotocol/inspector     # connect to http://localhost:8100/mcp
```

From the Agent SDK inside Docker:

```python
from claude_agent_sdk import ClaudeAgentOptions
options = ClaudeAgentOptions(mcp_servers={"kyc": {"type": "http", "url": ctx.settings.mcp_url}})
```

The file is mounted into the container: restart it after editing with `docker compose restart mcp`.

---

## Documents for RAG

`data/documents/` (mounted read-only in the API container at `/data/documents`):

- `annual_reports/{uk,pt,br}/*.md`: annual-report extracts. UK in English, PT and BR in Portuguese. Some companies have two years; some have none.
- `policies/kyc_policy.md`: the bank's sample KYC policy, including **how risk points add up to a rating and a recommendation**.
- `policies/procedures_uk.md`, `procedimentos_pt.md`, `procedimentos_br.md`: country procedures (good candidates for Agent Skills).
- `index.json`: metadata for every document. About a third of the annual reports have **no registration number** in the index, so you have to find them by name or content.

The reports are written the way real ones are: labels vary (Turnover, Revenue, Sales; Volume de negócios, Vendas e serviços prestados...), some tables are **in thousands**, many include a **prior-year column**, figures also appear in the narrative, and holding companies publish **consolidated group accounts** that mention their subsidiary. Read carefully: the latest year, the right entity, the right units.

Postgres has pgvector enabled (`CREATE EXTENSION vector` already ran). Create your own tables for chunks and embeddings. Everything except the Claude API and optional web search should work offline, so prefer a local multilingual embedding model:

```bash
docker compose --profile embeddings up -d ollama
docker compose exec ollama ollama pull bge-m3          # multilingual, 1024 dimensions
```

Then call it from the API container:

```python
import httpx
r = httpx.post(f"{ctx.settings.ollama_url}/api/embed", json={"model": "bge-m3", "input": ["texto", "text"]})
vectors = r.json()["embeddings"]
```

---

## Tracing with Langfuse

```bash
docker compose --profile tracing up
```

Starts Langfuse v4 (web, worker, Postgres, ClickHouse, Redis, MinIO; allow about 4 GB of RAM) with a ready-made project. Open http://localhost:3001 and log in with `academy@example.com` / `academy-local`. The API container already has `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` from `.env`, and the `langfuse` Python package installed.

Observability is the top bonus: every agent step traceable, **with personal data hidden** (mask it client-side before it leaves your app). `services/api/app/tracing.py` gets you started:

```python
from claude_agent_sdk import ClaudeAgentOptions
from app.tracing import agent_sdk_otel_env, langfuse_client

options = ClaudeAgentOptions(env=agent_sdk_otel_env(service_name="kyc-registry-agent"), ...)  # Agent SDK spans -> Langfuse
langfuse = langfuse_client()   # your own spans; everything passes mask_pii() first
```

`mask_pii()` is yours to implement. Agent SDK spans are structural by default (names, timings, tokens); don't switch on the `OTEL_LOG_*` content options unless your masking covers them.

---

## Scoring

```bash
# Full evaluation: submit every company, wait, score
docker compose run --rm scoring --run-all --label my-change

# Quick happy-path check in one country
docker compose run --rm scoring --run-all --country UK --difficulty straightforward

# Score existing runs without starting new ones
docker compose run --rm scoring --latest

# Prompt optimisation: change one thing, re-run, compare with the baseline report
docker compose run --rm scoring --run-all --label prompt-v2 --compare scoring/reports/report-<timestamp>-baseline.json
```

Or without Docker: `pip install -e packages/kyc_common httpx && python scoring/score.py --run-all`.

For each company the tool checks:

| Field | How |
|---|---|
| `schema` | The pack validates against the schema |
| `sourcing` | Every fact has a source that exists |
| `legal_name`, `registration_number`, `status`, `address` | Against the register (normalised) |
| `directors` | F1 against the directors (secretaries excluded) |
| `beneficial_owners` | F1 against the UBOs (25% or more, look-through); where UBOs can't be identified, a `ubo_unidentified` flag is expected |
| `adverse_news` | F1 of adverse items against the relevant adverse articles; news about similarly named companies counts against you |
| `financials` | Latest year, revenue and net profit within 1% |
| `risk_rating`, `recommendation` | Against the KYC policy applied to the true facts |
| `flags` | F1 against the expected flag types |

Reports land in `scoring/reports/` as Markdown and JSON, with accuracy per field, per segment (country, difficulty, case) and per company, plus **latency and cost per run** (cost comes from `ctx.record_metrics()`). With `--compare` the report adds before and after per field, and lists the companies that improved and regressed, on the companies both runs share.

The placeholder scores about 66% overall: identity fields are mostly right, and everything that needs judgement (owners, news, financials, risk) is wrong.

---

## The generated data

`data/generated/` is committed so everyone scores against the same set, and regenerated with:

```bash
python data/generator/generate.py --seed 2026 --per-country 50
```

150 companies (50 per country). 60 are straightforward; 90 have challenges built in:

| Case | What happens |
|---|---|
| `holding_chain` | Owned through a holding company; look through to the UBOs |
| `offshore` | A shareholder in a high-risk jurisdiction with no register: UBOs can't be identified |
| `conflicting_data` | The bank's onboarding request disagrees with the register (number, address or directors) |
| `missing_address`, `no_annual_report` | Required information is missing |
| `negative_news`, `sanctions` | Adverse media, medium or high severity, in the local language |
| `name_collision` | Adverse news about a different company with a similar name |
| (any company) | About a quarter of companies also have stories that **sound** alarming but are not adverse: the company was the victim of fraud, won a lawsuit or was cleared by a regulator |
| `namesake` | A dissolved company with a near-identical name in the register or the bank's records |
| `inactive` | Dissolved, in liquidation, insolvent or *baixada* |
| `prohibited_sector`, `high_risk_sector` | Gambling; money services or digital assets |

The press refers to companies in different ways (legal name, short name, trading name), so search broadly, then confirm the article is about the right company.

`data/generated/answers.json` holds the correct answer for every company, used by the scoring tool.

---

## Rules

- Keep keys and passwords out of the code: use `.env`, which is git-ignored.
- A human always makes the final decision.
- Don't use coding agents to do all the work for you.
- Don't read `answers.json` (or the generator's internals) from your pipeline. That's the exam paper.

## Troubleshooting

| Problem | Fix |
|---|---|
| `env_file ... required` error | Update Docker Desktop (Compose 2.24+ needed). |
| Port already in use | Change the left side of the port mapping in `docker-compose.yml`, e.g. `"3002:80"`. |
| `database: false` in `/health` | `docker compose logs db`. To reset the database: `docker compose down -v` (deletes runs). |
| Changes to `db/init.sql` not applied | It only runs on an empty volume: `docker compose down -v && docker compose up`. |
| Runs stuck in `running` | `docker compose logs -f api`. Pipelines time out after `PIPELINE_TIMEOUT_S`. |
| Langfuse slow to start | First start runs migrations; give it a minute. Needs about 4 GB of RAM for Docker. |
| Agent SDK returns nothing / exits immediately | Check you are not running as root with `bypassPermissions`, and that `ANTHROPIC_API_KEY` is set (`/health` shows `anthropic_key_configured`). |
| Permission errors writing files from the API container | The container user is uid 1000. Write to `/tmp` or your home directory, not to `/data` (read-only). |

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e packages/kyc_common -r services/api/requirements.txt -r services/mockdata/requirements.txt pytest
pytest tests        # scoring sanity checks: the correct answers score 100%
```
