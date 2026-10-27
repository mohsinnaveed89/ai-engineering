# Command cheat sheet

All commands run from the repo root (`ai-academy-starter-kit/`).

## Start and stop

| Command | What it does |
|---|---|
| `docker compose up --build` | Builds and starts web :3000, api :8000, mockdata :8001, mcp :8100 and postgres :5432. |
| `docker compose --profile embeddings up -d ollama` | Starts local Ollama for embeddings. |
| `docker compose exec ollama ollama pull bge-m3` | Downloads the embedding model, once. |
| `docker compose --profile tracing up` | Adds Langfuse on :3001 (about 4 GB RAM). |
| `docker compose ps` / `docker compose logs -f mcp` | Shows container status, or follows one service's logs. |
| `docker compose down` | Stops the stack and keeps the DB data. |
| `docker compose down -v` | Stops the stack and wipes the DB volume. This is the only way `db/init.sql` runs again. |

## Running a company pipeline

| Command | What it does |
|---|---|
| `docker compose exec api python -m app.cli "Hawksmoor Meridian Textiles" UK` | Runs the pipeline for one company and scores it. No web app or DB is needed. This is the fastest loop. |
| `docker compose exec api python -m app.cli --company-id BR-012 --out /tmp/pack.json` | Runs a company by its challenge id and saves the pack. |
| `docker compose exec api python -m app.cli --company-id BR-012 --previous /tmp/pack.json --feedback "Check the UBOs"` | Simulates a human sending the pack back with feedback. |
| `docker compose exec api python -m app.cli --company-id UK-001 --compare-out /tmp/UK-001.md` | Runs a test-set company and writes a Markdown table of expected (`answers.json`) vs. actual values, with a score per field. Skipped if the company is not in the test set. |
| `docker compose exec -T api cat /tmp/UK-001.md > UK-001-compare.md` | Copies a file from the container's `/tmp` to your machine. `-T` avoids terminal artefacts in the redirect. Also works for `--out` packs. |
| `docker compose cp api:/tmp/pack.json ./pack.json` | Another way to copy a file out of the container. |
| `docker compose exec -T api python -m app.cli --company-id UK-001 --json > UK-001.json` | Prints the pack JSON to stdout and saves it locally. Progress and scores go to stderr and stay out of the file. |
| Web app at http://localhost:3000 | Submits a company, then approve, decline or send back. |

## Testing a human send-back (slice 4)

Each live run calls the real API (about $0.20 to $0.50). Use a different `--out` per scenario so `/tmp/v1.json` is never overwritten. Details and expected results: [docs/slice-4-human-review.md](docs/slice-4-human-review.md).

| Command | What it does |
|---|---|
| `docker compose exec api python -m app.cli --company-id UK-020 --out /tmp/v1.json` | Builds the first pack to send back. |
| `docker compose exec api python -m app.cli --company-id UK-020 --previous /tmp/v1.json --feedback "The two supplier-of-the-year articles look like they are about a different company. Please re-check them and remove any that are not about this company." --out /tmp/v2.json` | Sends v1 back with a comment that has something to fix. Triage maps it to `news`, only the news agent re-runs, and the other sections must stay as in v1. |
| `docker compose exec api python -m app.cli --company-id UK-020 --previous /tmp/v1.json --feedback "The UBOs behind the holding company are missing" --out /tmp/v3.json` | A comment whose premise is wrong for UK-020 (no holding company). Triage marks it `ambiguous`, and only `notes` should change, with no invented owner. |
| `docker compose exec api python -m app.cli --company-id UK-020 --previous /tmp/v1.json --feedback "Make it low risk" --out /tmp/v4.json` | An `unsupported` request. Nothing in the pack should change. |
| `docker compose exec api python -m app.cli ... --review-out /tmp/review.json` | Changes where the reviewer's feedback is written (default `/tmp/review-feedback.json`). The triage is the first entry, then one entry per review round. |
| `docker compose exec -T api python -c "import json;a,b=json.load(open('/tmp/v1.json')),json.load(open('/tmp/v2.json'));[print(f'{k:18}','same' if a[k]==b.get(k) else 'DIFFERENT') for k in a if k!='generated_at']"` | Shows which sections of v2 differ from v1. Only the sections the comment asked for (plus `notes`) should be `DIFFERENT`. |
| `docker compose exec -T api cat /tmp/review-feedback.json > review-feedback.json` | Copies the reviewer's feedback out of the container. |
| Web app at http://localhost:3000, then **Send back** | Same flow through the API. The new version is v2 and the earlier pack stays in `pack_history`. |

In the console, look for `Triage took Ns`, one `actionable | ambiguous | unsupported [owner] path` line per point, only the needed subagents being called, and `Round 1: pass`. `Triage unavailable (...)` means triage failed and the unrequested-change check is off for that run.

## Scoring

| Command | What it does |
|---|---|
| `docker compose run --rm scoring --run-all --label my-change` | Runs all 150 companies and writes a report to `scoring/reports/`. |
| `docker compose run --rm scoring --run-all --country UK --difficulty straightforward` | Scores only a subset. |
| `docker compose run --rm scoring --run-all --label v2 --compare scoring/reports/report-<ts>-<label>.json` | Compares against an earlier report. The baseline is `report-20261019-161453-baseline.json`. |
| `docker compose run --rm scoring --latest` | Shows the latest report. |

## After you change code

What you edited decides which command to run.

| You changed | Command | Why |
|---|---|---|
| `services/mcp/server.py` | `docker compose restart mcp` | The file is bind-mounted, so a restart is enough. |
| `services/api/app/**` (pipeline, agents, prompts) | nothing | It is mounted and uvicorn reloads on save. |
| `packages/kyc_common/**` (schema, RAG, mockdata client) | `docker compose up -d --build mcp api` | The package is pip-installed into each image at build time and is not mounted. The container keeps the old copy until you rebuild. Rebuild mockdata too if it imports the package. |
| `services/api/requirements.txt` | `docker compose up --build api` | New Python dependencies. |
| `services/mcp/requirements.txt` | `docker compose up --build mcp` | New Python dependencies. |
| `docker-compose.yml` (env, volumes) | `docker compose up -d mcp` | Recreates the changed service. |
| `db/init.sql` | Run the DDL by hand with `docker compose exec -T db psql -U academy -d academy -c "..."`, or `down -v` | `init.sql` only runs on an empty volume. |

## Document search (RAG)

| Command | What it does |
|---|---|
| `docker compose exec api python -m kyc_common.RAG.ingest` | Chunks the reports and policies, embeds them with bge-m3 and upserts them into `document_chunks`. Re-run it after changing chunking or the embed prefix. |
| `docker compose exec api python -m kyc_common.RAG.search "Hawksmoor revenue 2024"` | Tests vector + BM25 + RRF from the CLI. |
| `docker compose exec db psql -U academy -d academy -c "select count(*) from document_chunks"` | Checks the index is filled. |

## Testing the MCP server

| Command | What it does |
|---|---|
| `npx @modelcontextprotocol/inspector` | Opens the Inspector UI (usually http://localhost:6274). Choose Streamable HTTP and connect to `http://localhost:8100/mcp`. |
| `docker compose exec mcp python -c "from kyc_common.RAG.search import search; print(search('...'))"` | Checks the import and search work inside the mcp container. |

## Local tests

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e packages/kyc_common -r services/api/requirements.txt -r services/mockdata/requirements.txt pytest
pytest tests                      # scoring sanity checks
pytest tests/test_scoring.py -k <name>
pytest tests/test_review_human.py   # slice 4: send-back triage and unrequested changes (no LLM, under a second)
```
