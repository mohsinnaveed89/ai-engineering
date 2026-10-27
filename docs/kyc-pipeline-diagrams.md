# KYC pipeline: components and how they run

Diagrams of the starter kit as it is wired today. Source of truth: `docker-compose.yml`, `services/api/app/pipeline.py`, `services/api/app/agents/kyc_agent.py`, `services/api/app/agents/risk_check.py`, `services/mcp/server.py`.

Contents
1. Services (docker compose)
2. One run, end to end
3. The agent team
4. Tools and where their data comes from
5. Timeline of one run (UK-020 as the example)
6. Validation and the one retry
7. Permissions: who may call what
8. Running one company from the CLI, and scoring
9. Things worth knowing

---

## 1. Services (docker compose)

```
                        you (browser)                      you (terminal)
                             │                                   │
                             ▼                                   │ docker compose exec api
                  ┌─────────────────────┐                        │   python -m app.cli
                  │ web          :3000  │                        │
                  │ nginx, static files │                        │
                  └──────────┬──────────┘                        │
                             │ REST + SSE                        │
                             ▼                                   ▼
                  ┌──────────────────────────────────────────────────────┐
                  │ api  :8000   FastAPI                                  │
                  │   routes, run storage, run_pipeline(ctx), Agent SDK   │
                  │   mounts: ./services/api/app (hot reload), ./data     │
                  └───────┬──────────────────────────────┬───────────────┘
                          │ SQL                          │ MCP over HTTP (tools)
                          ▼                              ▼
              ┌─────────────────────┐        ┌──────────────────────────┐
              │ db        :5432     │◄───────│ mcp          :8100       │
              │ postgres + pgvector │ vector │ FastMCP server.py         │
              │  • runs, reviews    │ search │  (bind-mounted file)      │
              │  • policy/report    │        └───────┬──────────┬───────┘
              │    document index   │                │          │ HTTPS
              └─────────────────────┘                │          ▼
                          ▲                          │   Tavily (live news,
                          │ embeddings               │   only if TAVILY_API_KEY)
                          │                          ▼
              ┌─────────────────────┐        ┌──────────────────────────┐
              │ ollama   :11434     │        │ mockdata     :8001       │
              │ bge-m3 embeddings   │        │ offline registers (UK/PT/│
              │ profile: embeddings │        │ BR), news, bank systems  │
              └─────────────────────┘        │ reads ./data (read-only) │
                                             └──────────────────────────┘

  other profiles:   scoring (tools)   one-shot job, compares packs with answers.json
                    tracing           Langfuse :3001 (+ its own postgres, clickhouse, redis, minio)
```

The api never reads `data/generated/answers.json`. Only the scoring tool does.

---

## 2. One run, end to end

```
 analyst                web                 api                          pipeline
   │  name + country     │                   │                               │
   ├────────────────────►│  POST /runs       │                               │
   │                     ├──────────────────►│  create run (queued)          │
   │                     │                   ├─ status: running ────────────►│ run_pipeline(ctx)
   │                     │  GET /runs/{id}/stream (SSE)                      │   │
   │◄── live progress ───┤◄── ctx.emit(...) ─┤◄──────────────────────────────┤   │  agent team (section 3)
   │                     │                   │                               │   ▼
   │                     │                   │◄───────── KycPack ────────────┤  checked pack
   │                     │                   ├─ store pack, status:          │
   │                     │                   │   awaiting_review             │
   │  approve / decline / send back          │                               │
   ├────────────────────►│  POST /runs/{id}/review                           │
   │                     ├──────────────────►│  approved | declined          │
   │                     │                   │  or sent_back ─► new version, │
   │                     │                   │  feedback + previous_pack in  │
   │                     │                   │  ctx, back to running         │
```

```
 queued ──► running ──► awaiting_review ──► approved
               ▲              │        └──► declined
               │              ▼
               └────────── sent_back          (any step can end in: failed; pipeline has a timeout)
```

A human always makes the final decision: the pack's `review.status` stays `pending` until then.

---

## 3. The agent team

One Claude Agent SDK session. The orchestrator can only look up the company and delegate. Specialists do the evidence work.

```
                         ┌───────────────────────────────────┐
                         │          ORCHESTRATOR             │
                         │ tools: Agent, Skill + register     │
                         │   lookups only                     │
                         │ output: structured KycPack         │
                         │ max 30 turns                       │
                         └───────────────┬───────────────────┘
        1. identify the company          │
           Skill (kyc-uk / kyc-pt / kyc-br procedure)
           uk_search_companies / registry_search_company, registry_get_company
                                         │
        2. one turn, in parallel:        │
     ┌───────────────┬───────────────────┼─────────────────┬────────────────┐
     ▼               ▼                   ▼                 ▼                │
┌──────────┐   ┌──────────┐      ┌──────────────┐   ┌─────────────┐        │
│ownership │   │  news    │      │    bank      │   │ financials  │        │
│15 turns  │   │12 turns  │      │ 6 turns      │   │ 8 turns     │        │
│          │   │          │      │              │   │             │        │
│ officers │   │ news_    │      │ compare_     │   │ summary_get_│        │
│ owners   │   │  search  │      │  register_   │   │  financial_ │        │
│ UBO walk │   │ news_    │      │  with_bank   │   │  statements │        │
│ policy_  │   │  search_ │      │  (in-process)│   │ search_     │        │
│  search  │   │  web     │      │ bank_get_    │   │  documents  │        │
│          │   │          │      │  relationship│   │             │        │
│ returns: │   │ returns: │      │ returns:     │   │ returns:    │        │
│ officers │   │ news,    │      │ flags,       │   │ financial_  │        │
│ owners   │   │ flags,   │      │ sources      │   │ summary,    │        │
│ flags,   │   │ sources  │      │              │   │ flags,      │        │
│ sources  │   │          │      │              │   │ sources     │        │
└────┬─────┘   └────┬─────┘      └──────┬───────┘   └──────┬──────┘        │
     └──────────────┴───────────────────┴──────────────────┘               │
                                         │ all JSON merged by the orchestrator
        3. one call, after the four       ▼
           have returned:          ┌───────────────┐
                                   │   EVALUATOR   │   policy_search (several times)
                                   │   10 turns    │   bank_get_risk_appetite
                                   │               │
                                   │ returns:      │   applies policy sections 3-9:
                                   │ risk, flags,  │   points, rating, recommendation,
                                   │ sources       │   typed flags, policy sources
                                   └───────┬───────┘   (each with a verbatim excerpt)
                                           ▼
        4. orchestrator merges, de-duplicates, drops unsupported typed flags,
           returns StructuredOutput  ──►  validation (section 6)
```

Rules every specialist is given: no source, no claim; flag, don't judge; prefix source ids (`own-`, `news-`, `bank-`, `fin-`, `eval-`); reply with one JSON object only.

---

## 4. Tools and where their data comes from

```
 AGENT           TOOL (mcp__kyc__ unless noted)            BACKED BY
 ─────────────   ───────────────────────────────────────   ─────────────────────────────────────────
 orchestrator    uk_search_companies                       mockdata /uk register search
                 registry_search_company                   mockdata registers (UK / PT / BR)
                 br_find_prospect_cnpj                     mockdata (BR is CNPJ lookup only)
                 registry_get_company                      mockdata register record
 ownership       registry_get_officers_and_owners          mockdata register
                 registry_trace_ultimate_owners            code walk of the chain: one register lookup per
                                                           corporate owner, % multiplied, depth 3
                 registry_get_company, policy_search       (see above / below)
 news            news_search                               mockdata /news/search (archive).
                                                           Strips legal suffix first (Ltd, PLC, ...);
                                                           archive needs every query word to match
                 news_search_web                           Tavily live web (needs TAVILY_API_KEY)
 bank            mcp__bankcheck__compare_register_with_bank  in-process SDK tool (bank_conflicts.py):
                                                           register record vs bank prospect record,
                                                           normalised compare, emits conflicting_data flags
                 bank_get_relationship                     mockdata bank system
 financials      summary_get_financial_statements          document index (annual-report extracts)
                 search_documents                          document index (vector search)
 evaluator       policy_search                             vector search over kyc_policy.md, returns the
                                                           best matching numbered section + its text
                 bank_get_risk_appetite                    mockdata bank system (prohibited / high-risk lists)
```

```
   mcp server ──► mockdata :8001 ──► data/generated/*.json     (registers, news.json, bank.json, companies.json)
        │
        └────────► db (pgvector)  ──► index of data/documents/  (policy, annual reports)
                        ▲
                        └── embeddings from ollama (bge-m3)
```

`policy_search` returns one section per call, plus headings of other possibly relevant sections in `also_relevant`. That is why the evaluator calls it many times.

---

## 5. Timeline of one run (UK-020, from the CLI log)

```
 t(s)   who            what
 ─────  ─────────────  ──────────────────────────────────────────────────────────────
   3    orchestrator   Skill (country procedure), uk_search_companies
   7    orchestrator   registry_get_company
  11    orchestrator   starts ownership, news, bank, financials (parallel)
  12-18 subagents      register + policy, news archive + web, bank check + relationship, financials
  20-27 orchestrator   the four subagents return
  35    orchestrator   starts evaluator
  36-54 evaluator      policy_search x8, bank_get_risk_appetite
  58-67 orchestrator   evaluator returns
  65-82 orchestrator   StructuredOutput (final pack)
  82    agent          validation (section 6). Pass: done. Fail: the retry below.
  83+   orchestrator   fix round: new StructuredOutput, sometimes a re-run of the evaluator
```

Most of the wall-clock time is the evaluator plus the orchestrator assembling the final pack.

---

## 6. Validation and the one retry

Runs in `_check()` in `kyc_agent.py` on the orchestrator's final `StructuredOutput`.

```
  result (ResultMessage)
        │
        ▼
  structured_output missing? ──yes──► "You did not return a pack"  ─┐
        │ no                                                        │
        ▼                                                           │
  KycPack.model_validate  ──schema error──► "does not match schema" ─┤
        │ ok                                                        │
        ▼                                                           │
  sourcing_problems     every fact cites a source id in pack.sources │
        │ none                                                      │
        ▼                                                           │
  ubo_problems          UBOs are people; owners but no UBO needs     │
        │ none          a ubo_unidentified flag                      │
        ▼                                                           │
  risk_problems         points add up to the rating (policy 8);      │
        │               recommendation follows policy 9;             │
        │               citation_problems (below)                    │
        │ none                                                      │
        ▼                                                           │
  VALID PACK                                               problems ┘
        │                                                       │
        │                                                       ▼
        │                                 first time?  yes ─► send the problem text back to the
        │                                  (and not max_turns)  SAME session: "Return the corrected pack"
        │                                                       │ then _check again
        │                                                       ▼
        │                                              still invalid ─► run_kyc_agent returns None
        ▼
  warnings (policy 9 not cited), review.status = "pending",
  generated_by = "agent-team-v2 (<model>)"
```

`citation_problems` (inside `risk_problems`), for the policy sources that risk factors cite:

```
  1. was any policy section retrieved?          no  ─► "no policy section was retrieved"
  2. points used? then section 8 must be cited  no  ─► "cite policy section 8"
  3. per cited policy source:
       section returned by policy_search?       no  ─► "policy_search did not return it"
       excerpt passes the quote check?          no  ─► "excerpt is not quoted verbatim ..."
         quote check = at least 80% of the excerpt's words appear in that section's text
         (MIN_WORD_OVERLAP in risk_check.py; markdown, order, punctuation and "..." ignored)
```

`retrieved` is filled from every `policy_search` result during the session (subagents included) and kept across the retry. The latest text per section wins.

---

## 7. Permissions: who may call what

Permissions are session-wide, so `allowed_tools` is the union of every subagent's tools. A PreToolUse hook then narrows the orchestrator.

```
   any mcp__kyc__* tool call
            │
            ▼
   _orchestrator_tool_guard (hook)
     ├─ call comes from a subagent (has agent_id)?          ─► allow
     ├─ not a kyc tool?                                      ─► allow
     ├─ in ORCHESTRATOR_TOOLS (register lookups)?            ─► allow
     └─ otherwise                                            ─► DENY
            "<tool> is not available to the orchestrator. Delegate it to the
             matching subagent (ownership, news, bank, financials, evaluator)."
```

Built-in tools: only `Agent` and `Skill`. This is why an orchestrator that tries `policy_search` during a fix round is refused.

---

## 8. Running one company from the CLI, and scoring

```
  docker compose exec -T api python -m app.cli --company-id UK-020 --compare-out /tmp/UK-020.md
        │
        ▼
  look up the company in the test set ─► PipelineContext (run_id, name, country, emit → terminal)
        │
        ▼
  run_pipeline(ctx) ──► run_kyc_agent(ctx) ──► KycPack | None
        │
        ▼
  KycPack.model_validate(result)   ◄── crashes on None (see section 9)
        │
        ├─ sourcing check
        ├─ score against data/generated/answers.json (scoring/score.py)
        └─ write the comparison table to /tmp/UK-020.md
              copy it out:  docker compose exec -T api cat /tmp/UK-020.md > UK-020-compare.md
```

Full scoring: `docker compose run --rm scoring --run-all --label <name> [--compare <older report>]`, reports in `scoring/reports/`.

Per-field score: identity, directors, UBOs, news F1, financials within 1%, risk and recommendation against the policy, flags.

---

## 9. Things worth knowing

- **`run_pipeline` returns `None` when the agent fails.** The docstring promises a placeholder fallback, but `pipeline.py` only returns `pack`, and the `build_placeholder_pack` import is unused. The CLI then fails on `KycPack.model_validate(None)`, which hides the real reason.
- **Debug logging** in `kyc_agent.py` prints a `debug:` line per `policy_search` and, on a failed check, per cited excerpt. It also writes `/tmp/kyc_debug_attempt<N>.json` inside the api container.
- **Two alternate agent modules exist:** `kyc_agent_tools.py` (specialists wrapped as tools) and `kyc_agent_test.py`. Only `kyc_agent.py` is imported by `pipeline.py`.
- **MCP changes need a restart.** `services/mcp/server.py` is a single-file bind mount, and an editor that replaces the file can leave the container holding the old copy. After editing it run `docker compose restart mcp`, then confirm the new code is live.
- **The api reloads on save,** but only `services/api/app` is mounted. New Python dependencies need `docker compose up --build api`.
