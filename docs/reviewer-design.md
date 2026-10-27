# Reviewer agent: design

Status: **design agreed; built in vertical slices.**
- **Slice 1 (done):** `ReviewFeedback` models, the code stage for the existing checks (schema, sourcing, UBO, risk arithmetic, policy citations against the DB), the loop in `run_kyc_agent` in place of `_check`, `PackRejected`, CLI and API surfacing, settings, tests.
- **Slice 2 (done):** capture of each specialist's reply and every tool call (`review_capture.py`), the coverage checklist (`missing[]`), news flags against news sources, merge fidelity, flag attribution to the specialist that raised them, and tool-log notes (`review_checks.py`).
  - Where it differs from the sections below: a `missing[]` item **fails the review**, like a blocking finding, because it is a policy requirement the pack silently omits. The "legal suffix in the news query" suggestion was dropped, since the MCP now strips the suffix itself; the log notes are tool errors and replies that were not JSON. "Every specialist ran" is only checked on a fresh run, because a human revision re-runs only some.
- **Slice 3 (done):** the LLM stage: `prompts/reviewer.md`, `agents/reviewer_llm.py` (team description generated from `SUBAGENTS`, full policy from the DB, its own session with only `policy_search` and `bank_get_risk_appetite`, structured output, merge rules), wired into the loop.
  - Where it differs from the sections above: the LLM stage runs on every parseable pack, including one the code stage already failed (so one send-back carries everything). A missing item from the LLM needs evidence, or it becomes a suggestion. Evidence can be a pack source id or any policy section (`kyc_policy.md#N`). If the LLM stage fails or times out, the code stage's verdict stands and a suggestion says so. Settings added: `REVIEW_LLM` (on by default), `REVIEWER_MODEL` (defaults to `CLAUDE_MODEL`), `REVIEWER_TIMEOUT_S`.
- **Slice 4 (next):** human triage, the send-back flow for human comments, and the regression check.
- Later: final housekeeping (diagrams doc, scoring run).

Contents
1. Goal and what changes
2. Decisions
3. Where the reviewer sits
4. Auto-review loop
5. Inside the reviewer: code stage and LLM stage
6. Human send-back flow
7. The feedback object
8. What the code stage checks
9. Blocking vs advisory
10. The reviewer prompt
11. How a failure is surfaced
12. Settings and files
13. Tests and acceptance
14. Risks and open items
15. Implementation order

---

## 1. Goal and what changes

Today the pack is checked by `_check()` inside `run_kyc_agent`, and a failure triggers one hard-coded retry with a text blob of problems. Problems seen in practice:
- The failure message is a string, so the orchestrator cannot tell which specialist caused it.
- Only the final pack is checked. Nothing compares it with what each specialist actually returned.
- A human send-back is just a free-text `<feedback>` tag on a full rebuild. Nothing maps the comment to a section.
- When the retry fails, `run_kyc_agent` returns `None` and the CLI and API crash with `model_validate(None)`.

The change: a separate, skeptical **reviewer** that checks the pack and everything behind it, and returns one structured **feedback object** to the orchestrator. The same reviewer is reused to triage human send-back comments.

```
BEFORE                                      AFTER
──────                                      ─────
run_kyc_agent                               run_kyc_agent
  turn                                        turn
  _check(result, retrieved)  ◄─ strings       reviewer.review(...)   ──► ReviewFeedback (structured)
  one hard-coded retry                        send back up to REVIEW_MAX_SENDBACKS (default 1)
  _log_policy_mismatch                        ReviewFeedback replaces the debug logging
  failure ─► return None ─► crash             failure ─► PackRejected(feedback) ─► clear report
```

---

## 2. Decisions

| # | Decision |
|---|---|
| 1 | Reviewer is **hybrid**: a deterministic code stage does the mechanical checks, an LLM stage adds judgement. It returns one `ReviewFeedback`. |
| 2 | **Python drives the loop** (not the orchestrator). The cap is enforced in code. |
| 3 | Send-backs are configurable: `REVIEW_MAX_SENDBACKS`, default **1** (so at most 2 reviews). |
| 4 | When the cap is reached with blocking findings, the run **fails with a field-level feedback object** that says where and why. |
| 5 | Scope: `kyc_agent.py`, `risk_check.py`, new `agents/reviewer.py`. The alternate `kyc_agent_tools.py` and `kyc_agent_test.py` are left alone. The shared pack schema is **not** changed. |
| 6 | The reviewer sees: the pack, each subagent's returned JSON (labelled by name), a compact tool-call log (tool, inputs, error or not, result size), and the full policy. |
| 7 | Policy ground truth comes from the **vector DB** by exact section lookup, not from the markdown file and not from what the agents say they retrieved. |
| 8 | The LLM reviewer has the full policy in its prompt and read-only tools `policy_search` and `bank_get_risk_appetite` as a fallback. |
| 9 | Policy quotes: below 80% word overlap blocks. 80% or more but not an exact quote is an advisory finding. |
| 10 | News: **no independent re-query** (cost, especially live search). The reviewer judges what the agents saw. It adds suggestions from the tool-call log, and a deterministic check that news flags match news sources. |
| 11 | Human send-back: the reviewer **triages** the comment first. The orchestrator receives the feedback and **decides the routing**. Only needed specialists are re-run. Unrequested changes are detected. |
| 12 | Both stages run every round, so a single send-back carries every finding. Exception: a schema-invalid pack gets the code stage only. |
| 13 | LLM findings block only with concrete evidence (a source id plus the contradicting fact) in the `flags` or `policy` categories. Everything else is advisory. **Amended after live runs:** blocking is off by default (`REVIEW_LLM_BLOCKS=0`); the LLM stage only warns until a run history shows it is reliable. |
| 14 | The unrequested-change (regression) diff lives in the reviewer's code stage, as its own function. |
| 15 | If Postgres is unreachable the reviewer falls back to the text the agents retrieved and adds an advisory suggestion that policy verification was weaker. It does not fail the run. |
| 16 | The `/tmp/kyc_debug_attempt*.json` dump and `_log_policy_mismatch` are removed; the `ReviewFeedback` object carries the same information. |

---

## 3. Where the reviewer sits

```
 ctx (company, country, feedback?, previous_pack?, review_history?)
        │
        ▼
 run_kyc_agent ───────────────────────────────────────────────────────────────┐
   │                                                                           │
   │  [human send-back only]                                                   │
   │  reviewer.triage(comment, previous_pack, review_history) ─► ReviewFeedback│
   │                                                                           │
   ▼                                                                           │
 ┌──────────────────────────────────────────┐                                  │
 │ ORCHESTRATOR SESSION (Agent SDK)         │      captured while it runs:     │
 │   ownership  news  bank  financials      │ ───►  • each subagent's JSON     │
 │   evaluator                              │       • tool-call log            │
 │   final StructuredOutput = candidate pack│       • policy_search results    │
 └──────────────────┬───────────────────────┘                                  │
                    │ candidate pack + capture                                 │
                    ▼                                                          │
 ┌──────────────────────────────────────────┐                                  │
 │ REVIEWER                                 │                                  │
 │   code stage ──► LLM stage ──► merge     │                                  │
 └──────────────────┬───────────────────────┘                                  │
                    │ ReviewFeedback                                           │
        pass ◄──────┴──────► fail                                              │
         │                    │                                                │
         ▼                    ▼                                                │
   return pack        send back to the SAME session (cap: REVIEW_MAX_SENDBACKS)│
                              │ still failing at the cap                       │
                              ▼                                                │
                        raise PackRejected(feedback) ◄─────────────────────────┘
```

---

## 4. Auto-review loop

```
 round 1
   orchestrator builds pack ──► review ──► verdict pass? ──yes──► done
                                              │ no
                                              ▼
                              send-backs used < REVIEW_MAX_SENDBACKS ?
                                  │ yes                         │ no
                                  ▼                             ▼
   send <review_feedback> to the same session           PackRejected(feedback)
   orchestrator decides which specialists to re-run
   returns a corrected pack
                                  │
 round 2                          ▼
   review again ──► pass ──► done
                └─► fail, no send-backs left ──► PackRejected(feedback)
```

Special cases:
- **No pack at all** (no structured output, or the turn hit `max_turns`): no send-back, since asking again cannot help. Fails immediately with a blocking `schema` finding that says what the result was.
- **Schema-invalid pack:** the code stage returns the schema errors with paths and the LLM stage is skipped, because there is no parseable pack to judge. These errors are sent back like any other.
- Each send-back keeps the same session, so the orchestrator keeps its context.

```
 pseudocode (run_kyc_agent)

   capture = RunCapture()
   if ctx.feedback:
       triage = await reviewer.triage(ctx, capture)
   result = await run_turn(client, first_message(ctx, triage), capture)
   for attempt in range(settings.review_max_sendbacks + 1):
       feedback = await reviewer.review(ctx, result, capture, attempt)
       if feedback.verdict == "pass":
           return finalise(feedback.pack)
       if attempt == settings.review_max_sendbacks or feedback.fatal:
           raise PackRejected(feedback)
       result = await run_turn(client, send_back_message(feedback), capture)
```

---

## 5. Inside the reviewer: code stage and LLM stage

```
  inputs:  candidate pack · RunCapture (subagent JSON, tool-call log, retrieved policy)
           previous_pack (human mode) · ctx.review_history
              │
              ▼
 ┌────────────────────────── CODE STAGE (deterministic) ──────────────────────────┐
 │ 1. schema:      KycPack.model_validate, errors mapped to paths                  │
 │ 2. sourcing:    every fact cites a source id that exists (sourcing_problems)    │
 │ 3. ownership:   UBOs are people; ubo_unidentified rule (ubo_problems)           │
 │ 4. risk:        points sum = rating, recommendation rules (risk_problems)       │
 │ 5. policy:      each cited policy source vs policy section loaded from the DB  │
 │                 (<80% overlap blocks; not an exact quote = advisory)            │
 │ 6. coverage:    checklist per policy section, produces missing[] (section 8)    │
 │ 7. news:        news flags match news sources (section 8)                       │
 │ 8. merge:       subagent output was merged unchanged (bank conflicts kept, ...) │
 │ 9. regression:  human mode only, unrequested changes vs previous_pack           │
 │ 10. log:        tool-call patterns ─► suggestions (e.g. suffix in news query)   │
 └──────────────────────────────────┬─────────────────────────────────────────────┘
                                    │ findings (origin: code), missing[], suggestions[]
                                    ▼
 ┌────────────────────────── LLM STAGE (skipped if schema-invalid) ───────────────┐
 │ own session · prompts/reviewer.md · full policy in the prompt                   │
 │ tools: policy_search, bank_get_risk_appetite (read-only, fallback)              │
 │ gets the code-stage output as ground truth and does NOT redo mechanical checks  │
 │ judges: flags supported by evidence? sector / jurisdiction flags vs risk        │
 │ appetite? rationale consistent with rating? namesake or victim article wrongly  │
 │ counted? attributes each problem to the agent that produced it                  │
 └──────────────────────────────────┬─────────────────────────────────────────────┘
                                    │ findings (origin: llm), missing[], suggestions[]
                                    ▼
 ┌────────────────────────── MERGE (code) ─────────────────────────────────────────┐
 │ • drop duplicates (same path + rule)                                             │
 │ • downgrade an LLM finding to advisory unless it has evidence and is in the     │
 │   flags or policy category                                                       │
 │ • verdict = fail if any blocking finding, else pass                              │
 │ • derive fields[] roll-up (ok / missing / invalid per top-level pack field)      │
 └──────────────────────────────────┬─────────────────────────────────────────────┘
                                    ▼
                              ReviewFeedback
```

The LLM never re-checks quotes or arithmetic. Code is exact and free at that, and quote-checking is exactly where the model-based path was unreliable.

---

## 6. Human send-back flow

The reviewer is reused. `trigger` tells it which job it is doing.

```
 human: "Send back" + comment
        │
        ▼
 API starts version N+1:  ctx.feedback = comment, ctx.previous_pack, ctx.review_history
        │
        ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ 1. TRIAGE   reviewer(trigger = human_feedback)                           │
 │    in:  comment, previous_pack, review_history                           │
 │    out: findings (origin: human) that map each point of the comment to   │
 │         pack paths + a suggested owner; flags points the sources cannot  │
 │         support or that are ambiguous; marks still_unresolved when the   │
 │         same point was raised in an earlier version                      │
 │    verdict = revise                                                      │
 └──────────────────────────────┬───────────────────────────────────────────┘
                                ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ 2. REVISE   orchestrator (new session) receives                          │
 │      <feedback> raw comment   <review_feedback> JSON   <previous_pack>   │
 │    it DECIDES the routing:                                                │
 │      • re-runs only the specialists it judges necessary                  │
 │      • if evidence feeding risk changes (owners, news, flags) it also    │
 │        re-runs the evaluator                                              │
 │      • keeps every other section exactly as in previous_pack             │
 └──────────────────────────────┬───────────────────────────────────────────┘
                                ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ 3. VERIFY   the normal auto-review loop (section 4) on the new pack      │
 │    plus the regression check: anything changed that nobody asked for?    │
 └──────────────────────────────────────────────────────────────────────────┘
        │
        ▼
 pack v(N+1) stored; pack_history keeps v1..vN; ReviewFeedback stored as an event
```

Why the policy text comes from the DB: a human revision starts a fresh session, so nothing has been retrieved yet. If the evaluator is not re-run, the previous pack's `eval-policy-*` sources still have to be verified. The code stage loads them from the DB by section number.

### Unrequested-change detection

```
 previous_pack  ──┐
                  ├─► diff per section: company, officers, owners, financial_summary,
 new pack       ──┘                     news, flags, risk, sources

 allowed to change:  • sections named by a finding's path
                     • risk, if owners / news / flags / financial_summary changed
                     • sources (ids and lists), list ordering ignored
 anything else changed:
     company, officers, owners, financial_summary  ─► blocking  (category merge)
     news, flags, risk                             ─► advisory  (promote to blocking after we see real diffs)
```

---

## 7. The feedback object

Defined in `agents/reviewer.py` (a Pydantic model). It is **not** added to `kyc_common`, because the shared pack schema is frozen.

```
ReviewFeedback
 ├─ trigger        auto_review | human_feedback
 ├─ verdict        pass | fail | revise          (revise = human triage)
 ├─ round, max_sendbacks
 ├─ fatal          true when asking again cannot help (no pack, max_turns)
 ├─ findings[]  ───────────────────────────────────────────────┐
 ├─ missing[]   ─────────────────────────────────┐             │
 ├─ suggestions[]                                │             │
 └─ fields[]       roll-up per top-level field   │             │
                   { field, status: ok | missing | invalid, reason }
                                                 │             │
 Finding  ◄──────────────────────────────────────┼─────────────┘
 ├─ id, severity     blocking | advisory         │
 ├─ category         schema | sourcing | flags | policy | merge | human_feedback
 ├─ path             e.g. risk.factors[1].sources, sources[eval-policy-8].excerpt
 ├─ produced_by      orchestrator | ownership | news | bank | financials | evaluator | unknown
 ├─ rule             pack rule or policy section it breaks
 ├─ problem          one sentence
 ├─ expected, actual short snippets
 ├─ cause            model_error | tool_error | missing_data | unsupported_claim | merge_changed
 ├─ suggested_owner  orchestrator | <specialist>   (a suggestion; the orchestrator decides)
 ├─ origin           code | llm | human
 ├─ reviewer_comment the human's words (human origin only)
 └─ still_unresolved raised in an earlier version too

 MissingItem  ◄──────────────────────────────────┘
 ├─ path             pack field that should be there
 ├─ required_by      policy section or pack rule
 ├─ expected, found
 ├─ status           absent | null_without_flag | unsupported
 └─ suggested_owner

 Suggestion          { about, text, based_on }    advisory only, never blocks
```

Example, based on the failure from this chat:

```json
{
  "trigger": "auto_review", "verdict": "fail", "round": 1, "max_sendbacks": 1,
  "findings": [{
    "id": "F1", "severity": "blocking", "category": "policy",
    "path": "sources[eval-policy-8].excerpt",
    "produced_by": "evaluator", "rule": "kyc_policy.md#8 quoted from the policy",
    "problem": "Excerpt joins a table row to the Rating line and shares only 62% of its words with section 8.",
    "expected": "a span quoted from kyc_policy.md#8",
    "actual": "| Conflicting data | 1 | ... Rating: 0 to 1 = low ...",
    "cause": "model_error", "suggested_owner": "evaluator", "origin": "code"
  }],
  "missing": [{
    "path": "financial_summary", "required_by": "kyc_policy.md#7",
    "expected": "financial_summary, or a missing_data flag",
    "found": "null and no missing_data flag", "status": "null_without_flag",
    "suggested_owner": "financials"
  }]
}
```

---

## 8. What the code stage checks

**Coverage checklist (builds `missing[]`)**

| Policy section | Pack must have | Else |
|---|---|---|
| 3 Customer identification | legal name, registration number, status, address, or a `missing_data` flag | `null_without_flag` |
| 3 | a `conflicting_data` flag for every register vs bank difference the `bank` specialist reported | `absent` (also a merge finding) |
| 4 Ownership | officers; owners resolved to people with a UBO marked, or a `ubo_unidentified` flag; `complex_ownership` when any owner is a company | `absent` / `unsupported` |
| 5 Adverse media | see the news rules below | |
| 6 Sector | sector from the register; sector flags judged against the risk-appetite lists (LLM stage) | |
| 7 Financial summary | `financial_summary`, or a `missing_data` flag | `null_without_flag` |
| 8 Risk rating | each point-bearing factor cites section 8; points sum to the rating | blocking finding |
| 9 Recommendation | follows the decline / approve / investigate rule; cites section 9 | blocking / advisory |
| inactive company | an `inactive_company` flag when status is not active | `absent` |

**News and flags must agree (blocking)**

```
  pack.news (items with sentiment, severity, url, sources)       pack.flags (adverse_media)
        │                                                              │
        └──────────────────────── must agree ─────────────────────────┘

  • every adverse_media flag cites news-* sources that exist in pack.sources and appear in pack.news
  • every adverse news item is covered by an adverse_media flag
  • no adverse_media flag without an adverse item
  • flag severity matches policy section 5:   litigation, fines, labour, environmental = medium
                                              fraud, money laundering, bribery, sanctions, terrorist financing = high
  • decline for sanctions needs a high-severity adverse item behind it
  • an article excluded as a namesake is not in pack.news
```

**Merge fidelity**

- Every `conflicting_data` flag in the captured `bank` JSON is in the final pack (the orchestrator is told never to drop them).
- `officers`, `owners`, `news` and `financial_summary` equal what their specialist returned, apart from exact duplicates.
- Policy sources from the evaluator are present and their excerpts unchanged.

**Suggestions from the tool-call log**

- `news_search` called with a legal suffix in the query (`PLC`, `Ltd`, ...): "archive search needs every query word to match; consider the short name."
- A specialist returned an error or empty result and no flag says it could not be checked.
- The evaluator called `policy_search` for fewer sections than the pack cites.

These never block, because we chose not to make extra news calls.

---

## 9. Blocking vs advisory

| Finding | Severity |
|---|---|
| Schema errors | blocking |
| Source missing, or a fact without a source | blocking |
| UBO rules; points and rating arithmetic | blocking |
| Section 8 not cited when points are used; cited section not in the policy | blocking |
| Policy excerpt below 80% word overlap | blocking |
| Policy excerpt 80% or more but not an exact quote | advisory |
| News flag vs news source disagreement | blocking |
| Dropped or altered subagent output (merge fidelity) | blocking |
| Unrequested change to company, officers, owners or financial summary | blocking |
| Unrequested change to news, flags or risk | advisory (for now) |
| Section 9 not cited | advisory |
| LLM finding in `flags` or `policy` with a source id and the contradicting fact | blocking |
| Any other LLM finding | advisory |
| Suggestions | advisory |

Advisory findings are shown as warnings (event stream and CLI). They are not put into the pack, because the schema is frozen.

---

## 10. The reviewer prompt

File: `services/api/app/prompts/reviewer.md`, with runtime placeholders. Changes to it get a `CHANGELOG.md` entry, as the repo requires.

```
 reviewer.md
 ├─ Role         skeptical second reader; no claim without a cited source;
 │               judge only from the inputs; never fix, only report
 ├─ The team     (generated at runtime from the real SUBAGENTS definitions)
 │                 orchestrator steps 1 to 6
 │                 per specialist: job, tools, pack fields it owns, flag types it may raise,
 │                                 source-id prefix, what it must not do
 │                 hand-offs: orchestrator merges, evaluator scores last
 ├─ Ownership    short hand-written table: pack field ─► specialist ─► source prefix
 ├─ Attribution  who produced a bad field:
 │                 source-id prefix (reg- own- news- bank- fin- eval-) + captured subagent JSON
 │                 separate "the tool returned nothing" / "the agent ignored it" /
 │                 "the orchestrator changed it while merging"
 ├─ Failure modes seen in practice (hints, not rules)
 │                 legal suffix in news query ─► too few articles
 │                 paraphrased or joined policy excerpts
 │                 orchestrator retyping the evaluator's sources
 │                 typed flags raised without the policy condition
 ├─ Policy       the full kyc_policy text from the DB (all numbered sections)
 ├─ Severity     the blocking / advisory table above, and the evidence rule for LLM findings
 ├─ Modes        auto_review  |  human_feedback triage (map each point to paths and an owner)
 └─ Output       the ReviewFeedback JSON schema
```

The reviewer runs as its own session: no built-in tools, `allowed_tools` limited to `mcp__kyc__policy_search` and `mcp__kyc__bank_get_risk_appetite`. Its own model comes from `REVIEWER_MODEL`.

---

## 11. How a failure is surfaced

```
 PackRejected(feedback)   raised by run_kyc_agent; run_pipeline lets it through
        │
        ├─ CLI    prints the summary, writes <out>.review.json, exits non-zero
        │         (no more model_validate(None) crash)
        │
        └─ API    runs._execute catches it:
                    • runs.error  = readable summary (text column, shown in the web warning box):
                         Run failed review after 2 rounds.
                         [evaluator] sources[eval-policy-8].excerpt: <problem>
                         Missing: financial_summary (kyc_policy.md#7), suggested owner: financials
                    • SSE "error" event carries the full ReviewFeedback JSON, stored in run.events
                    • status = failed
                  no DB change and no web UI change
```

Version history: the API already keeps `pack_history` and the per-version `reviews`. The `ReviewFeedback` for each version is stored as an event, so history needs no schema change.

---

## 12. Settings and files

New settings in `services/api/app/settings.py` (environment variables, like the others):

| Setting | Env var | Default |
|---|---|---|
| `review_max_sendbacks` | `REVIEW_MAX_SENDBACKS` | 1 |
| `reviewer_model` | `REVIEWER_MODEL` | same as `CLAUDE_MODEL` |
| `review_quote_overlap` | `REVIEW_QUOTE_OVERLAP` | 0.8 |

Files:

| File | Change |
|---|---|
| `agents/reviewer.py` | **new**: models, code stage, LLM stage, merge, summary rendering |
| `prompts/reviewer.md` | **new**: the reviewer prompt |
| `agents/kyc_agent.py` | capture subagent JSON and the tool-call log in `_run_turn`; new loop; triage call; send-back message; `PackRejected`; "revise, don't rebuild" section in the system prompt; `_check` and `_log_policy_mismatch` removed |
| `agents/risk_check.py` | keep the checks; add structured variants that return paths. The string versions stay as thin wrappers so the existing tests keep passing. |
| `pipeline.py` | `run_pipeline` raises `PackRejected` instead of returning `None`; remove the dead placeholder import |
| `runs.py` | catch `PackRejected`, publish the feedback as event data, store the readable summary |
| `cli.py` | print the feedback, write `<out>.review.json`, exit non-zero |
| `settings.py` | the three settings above |
| `prompts/CHANGELOG.md` | entries for the orchestrator send-back section and the reviewer prompt |
| `tests/test_reviewer.py` | **new** |

Not touched: `packages/kyc_common` schema, `docs/pack.schema.json`, `kyc_agent_tools.py`, `kyc_agent_test.py`, the web app.

---

## 13. Tests and acceptance

1. **Unit tests, no LLM** (`pytest tests`): the checklist and `missing[]`, the news and flag consistency rules, the merge-fidelity check, the unrequested-change diff, the two-tier quote check, and the blocking and downgrade rules.
2. **Loop test with a fake reviewer:** a hand-written feedback object goes through the send-back path. Checks the cap, `fatal`, and that `PackRejected` carries the feedback.
3. **CLI scenarios** (`--previous v1.json --feedback "..."`): "the UBOs behind the holding company are missing", "revenue should be the 2025 figure", "this article is about a different company". Each should map to the expected section and owner, and leave the other sections unchanged.
4. **Acceptance on UK-020:**
   - Run `docker compose restart mcp` first, so the news search uses the current `server.py` (strips "PLC") and returns all four Blackwater articles.
   - Rerun `python -m app.cli --company-id UK-020 --compare-out /tmp/UK-020.md`.
   - Success: an `adverse_media` flag from `news-0039`; risk 2 points, medium, `investigate_further`; reviewer verdict `pass`.
5. **Full scoring** (`--run-all --compare` against the baseline report) only with your approval, since it costs API money.

---

## 14. Risks and open items

| Item | Note |
|---|---|
| Latency and cost | Each review adds one LLM call. With the default of one send-back that is up to two calls (roughly 15 to 30s each) on a run that takes about 100s. The reviewer can use a smaller model through `REVIEWER_MODEL`. |
| Flapping | An LLM reviewer can disagree with itself between runs. Mitigated by the evidence rule: it can only block with a source id and a concrete contradiction. |
| Retrieval gaps | We chose no extra news calls, so the reviewer cannot prove that an article exists but was never retrieved (the UK-020 case). It can only suggest from the tool-call log. The data-side fix is restarting `mcp`. |
| Database down | The code stage needs Postgres for the policy sections. **Open:** if it is unreachable, fall back to what the agents retrieved and add an advisory suggestion that policy verification was weaker, or fail the run? Recommended: fall back and say so. |
| Unrequested-change noise | News, flags and risk start as advisory. Promote to blocking after we see real diffs. |
| Prompt drift | The team section is generated from `SUBAGENTS`, and only the field-ownership table is hand-written. |
| Quote threshold | 0.8 may need tuning. The two-tier check makes loose quotes visible. |

---

## 15. Implementation order

1. **Models and code stage.** `ReviewFeedback`, structured variants of the existing checks, the coverage checklist, the news and flag rules, DB policy lookup. Unit tests.
2. **Replace `_check` with the code stage only** (no LLM yet), the capture in `_run_turn`, `PackRejected`, and the CLI and API surfacing. Verify on UK-020.
3. **LLM stage:** `reviewer.md`, the runtime team section, the merge and downgrade rules.
4. **Human flow:** triage, the send-back and `<review_feedback>` messages, "revise, don't rebuild" in the orchestrator prompt, the unrequested-change diff. CLI scenarios.
5. **Housekeeping:** settings, CHANGELOG, update [kyc-pipeline-diagrams.md](kyc-pipeline-diagrams.md), then the scoring run with your approval.

Each step can be run and checked on its own before the next.
