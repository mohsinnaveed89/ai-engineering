# Prompt changelog

One entry per prompt change: what changed, why, and the score before and after.

## 2026-10-19 · Day 1: identity extraction with Claude

- **Change:** `orchestrator.md` system prompt (role + the three pack rules). New identity-extraction user prompt in `pipeline.py::extract_identity`: copy legal name, registration number, status and address verbatim from `<registry_record>`, join split addresses with ", ", null when not stated. Structured output (JSON schema from the `CompanyIdentity` Pydantic model), validated with Pydantic, one retry with the validation error fed back.
- **Hypothesis:** Identity fields stay at or near the baseline (they were already copied by code), and the missing-address cases improve because the model returns null instead of an empty or partial address. The rest of the pack is unchanged (still the placeholder).
- **Before:** 66.0% overall (`report-20261019-161453-baseline`); legal_name 99.3%, registration_number 98.7%, status 98.7%, address 98.7%.
- **After:** spot check only, full run pending. UK-001 69%, PT-001 69%, BR-001 62%, with all four identity fields at 100% and sourcing OK. About $0.005 and 3 s per company on claude-sonnet-5-5.

## 2026-10-20 · Agent system prompt v2 + locked-down KYC agent

- **Change:** `orchestrator.md` rewritten with XML-tagged sections (`<role>`, `<rules>`, `<tools>`, `<workflow>`, `<kyc_policy>`, `<example_pack>`, `<output_format>`). The three rules are unchanged. `{{KYC_POLICY}}` and `{{EXAMPLE_PACK}}` are injected by `app/agents/prompt.py`; the example is `example_pack.json` (fictional company, validated by `tests/test_example_pack.py`). New `app/agents/kyc_agent.py`: one Agent SDK session with `tools=[]` (no built-ins), `allowed_tools=["mcp__kyc__*"]`, `max_turns=20`, structured output from the KycPack schema, one in-session retry on schema or sourcing errors. `run_pipeline` now only calls the agent (Day 1 `extract_identity` and registry fetch removed); on failure it returns the placeholder pack with a flag.
- **Hypothesis:** Tool-driven research with the policy in the prompt lifts directors/UBO/news/flags/risk well above the placeholder. Financials stay low: the agent has no document tool, so it leaves them null with a `missing_data` flag.
- **Before:** 66.0% overall (`report-20261019-161453-baseline`).
- **After:** pending. Not run yet (no Docker in the authoring environment). Next: CLI on UK-001, PT-001, BR-001, then `--run-all --label prompt-v2 --compare` against the baseline.

## 2026-10-20 · Financial statements tool

- **Change:** new tool `get_financial_statements` (`app/tools/kyc_tools.py`): returns the latest year's annual-report extract plus the list of years on file. Its description tells the agent to convert "in thousands" tables to full units, keep losses negative, flag consolidated (group) figures, and set `going_concern_uncertainty` only on an explicit material-uncertainty statement. `orchestrator.md` workflow gains step 7 for it; the "leave financials null" line now applies only when the tool returns no report.
- **Hypothesis:** financials go from ~0 (always null) to near 100% within the 1% tolerance, including thousands, multi-year and consolidated cases. Risk scoring may also improve if the policy has a going-concern factor.
- **Before:** 66.0% overall (placeholder baseline); agent v2 not yet scored.
- **After:** pending. Tool verified directly against the mock data API (UK multi-year picks 2025, PT consolidated flagged, unknown number returns empty). Full agent run needs ANTHROPIC_API_KEY.

## 2026-10-20 · Policy-exact risk and recommendation

- **Change:** `orchestrator.md` step 8 now spells out section 8/9 (list factors with exact points, sum, map to rating, then the recommendation rule) and the "prefer investigate_further when incomplete" line is replaced by "follow section 9". `example_pack.json` now includes a financial summary (thousands converted) instead of a missing-accounts flag. New `app/agents/risk_check.py`: after the sourcing check, the pack's rating must equal the sum of factor points and the recommendation must match section 9 (decline for inactive/prohibited sector, approve only when low); a violation goes back to the agent in the existing one-retry loop.
- **Hypothesis:** risk-vs-policy and flag scores rise, because arithmetic and the approve/decline rule are no longer left to the model. The check cannot verify sanctions-based decline or that every applicable factor was listed.
- **Before / After:** pending (no scored agent run yet).

## 2026-10-20 · Explicit UBO marking

- **Change:** `orchestrator.md` step 4 now says how to fill `owners`: companies in the chain with `is_ultimate_beneficial_owner` false, natural persons with effective ownership of 25% or more marked true with `via` and look-through `ownership_pct`, PSC bands use the lower bound, and `ubo_unidentified` when 25% cannot be traced. New `ubo_problems` in `app/agents/risk_check.py` (in the one-retry loop): a company marked as UBO, or owners with no UBO and no `ubo_unidentified` flag, goes back to the agent.
- **Hypothesis:** `beneficial_owners` rises from 0% (the placeholder never sets the UBO flag; the earlier prompt never asked for it) to near the directors score.
- **Before:** 0.0% beneficial_owners (`report-20261019-161453-baseline`).
- **After:** pending. Run `docker compose run --rm scoring --run-all --label ubo --compare scoring/reports/report-20261019-161453-baseline.json`.

## 2026-10-25 · Country KYC procedures as Agent Skills

- **Change:** the UK, PT and BR procedures (`data/documents/policies/procedures_uk.md`, `procedimentos_pt.md`, `procedimentos_br.md`) are now Skills `kyc-uk`, `kyc-pt`, `kyc-br` in `agent_home/.claude/skills/`, with the matching tool name added to each step. `kyc_agent.py` runs with `cwd=agent_home` and `skills="all"`. `orchestrator.md` mentions the Skills and step 1 tells the agent to load the one for the company's country first.
- **Hypothesis:** identity, directors and UBO scores rise for BR (CNPJ-only lookup) and for PT/UK chain look-through, because the country procedure is followed explicitly instead of inferred.
- **Before / After:** pending (no scored agent run yet).

## 2026-10-25 · Trim orchestrator steps now covered by the Skills

- **Change:** `orchestrator.md` workflow steps 1-7 no longer repeat the lookup procedure (search, register record, officers and owners, status, news language, which financial figures). Step 1 defers to the country Skill; the remaining steps keep only pack-filling rules (UBO marking, news matching and demo exception, bank comparison, financial conversions). Steps 8-9 unchanged.
- **Hypothesis:** scores hold with a shorter prompt and the procedure lives in one place; risk is the agent skipping the Skill.
- **Before / After:** pending. Compare against the run before this trim; if scores drop, restore the previous steps.

## 2026-10-26 · Deterministic ownership chain walker (`registry_trace_ultimate_owners`)

- **Change:** new MCP tool `registry_trace_ultimate_owners` (logic in `packages/kyc_common/kyc_common/ownership.py`) walks holding chains in code: one register lookup per corporate owner, percentages multiplied along the chain, depth limit 3 (arg, max 10), cycles cut, persons reached by several branches summed (namesakes kept apart by nationality/date of birth), UK PSC bands use the lower bound. Each owner returns `status`, `via` and the register lookups behind it (`sources`). Dead ends (offshore/foreign owner with no register, depth limit, cycle, unavailable register) stay as company owners with `untraced_pct` / `ubo_unidentified`; nothing is guessed. The `ownership` subagent prompt (`kyc_agent.py`, `kyc_agent_tools.py`) now calls it once instead of looping on `registry_get_officers_and_owners`, and must register and cite each lookup. Schema untouched.
- **Hypothesis:** `beneficial_owners` F1 rises on holding-chain and offshore cases (exact look-through maths, fewer turns/tokens), and `ubo_unidentified` / `complex_ownership` flags become consistent.
- **Before / After:** pending. Run `docker compose up --build mcp` then `docker compose run --rm scoring --run-all --label ubo-walker --compare scoring/reports/report-20261019-161453-baseline.json`.

## 2026-10-27 · Register vs bank-system conflicts flagged by code (`compare_register_with_bank`)

- **Change:** new in-process SDK tool (`app/agents/bank_conflicts.py`) for the `bank` subagent in `kyc_agent.py`. It fetches the register record and the bank's prospect (onboarding request) record, normalises case, accents, punctuation, legal-form words, 'SURNAME, First' order, number formats and address abbreviations, and compares name, registration number, address and directors. Every remaining difference becomes one `conflicting_data` flag with both values side by side and both sources cited (`bank-reg`, `bank-prospect`): registration number high, name and directors medium, address medium (city/postcode) or low (street/district only). No prospect record means no flag. The `bank` prompt copies the tool's flags and sources verbatim and keeps `bank_get_relationship` only for the existing-customer `other` flag; the orchestrator must not drop `conflicting_data` flags from `bank`. Schema untouched.
- **Hypothesis:** `flags` score rises on the `conflicting_data` cases (e.g. UK-020: Bristol register address vs Belfast declared address) because the comparison no longer depends on the model noticing the difference; risk is extra `conflicting_data` flags on companies the answer key does not expect.
- **Before / After:** pending. UK-020 first, then `docker compose run --rm scoring --run-all --label bank-conflicts --compare scoring/reports/report-20261019-161453-baseline.json` (only after approval: it costs API money).

## 2026-10-27 · Policy excerpt quoting (evaluator prompt, orchestrator step 5, `risk_check` validator)

- **Change:** debug logging showed the pack was rejected twice because `eval-policy-3` / `eval-policy-8` excerpts dropped markdown bold (`**conflicting data**`) and `eval-policy-8` joined a table row to the `Rating:` line with `...`. The orchestrator then tried `policy_search` (denied by the tool guard) and re-ran the evaluator. The `evaluator` prompt now asks for one contiguous span per source (one row or sentence), the orchestrator must add the evaluator's policy sources unchanged and may not search, and the rejection message says so. The validator's quote check is now a word-overlap test: at least 80% of an excerpt's words must appear in the section it cites (case, markdown, order, punctuation and `...` ignored); `REVIEW_QUOTE_OVERLAP` sets the share.
- **Hypothesis:** the first `StructuredOutput` validates, so the fix round and the extra evaluator runs disappear (UK-020 latency drops by ~30s) with no change in risk scores.
- **Before / After:** pending. Rerun UK-020 and confirm no `Pack invalid` line.

## 2026-10-27 · Reviewer replaces `_check`; structured send-back message (slice 1)

- **Change:** `agents/reviewer.py` checks every candidate pack (schema, sourcing, UBO rules, risk arithmetic, policy citations against the sections in the vector DB) and returns one `ReviewFeedback` (findings with path, producer, rule, cause, suggested owner). `run_kyc_agent` sends a failing pack back into the same session, `REVIEW_MAX_SENDBACKS` times (default 1), as `<review_feedback>` JSON plus an instruction: decide which subagent, if any, to re-run for each blocking finding, change nothing else, do not call `policy_search`, return the full pack. This replaces the old `"{problem}\nReturn the corrected pack."` text. A pack that still fails raises `PackRejected` with the feedback (CLI prints it and exits 1; the API stores a readable summary and publishes the JSON). Design: `docs/reviewer-design.md`. No LLM reviewer stage yet.
- **Hypothesis:** the orchestrator fixes the right section on the send-back because each finding names the path and the agent that produced it, so fewer blind evaluator re-runs; a failed run now says exactly which fields failed and why instead of crashing on `None`.
- **Before / After:** pending. UK-020 first (after `docker compose restart mcp`); full scoring only with approval.

## 2026-10-27 · Reviewer checks what the specialists returned (slice 2)

- **Change:** the reviewer now also sees every subagent's reply and tool call. New checks: a policy coverage checklist (`missing[]`: null identity field, inactive status, corporate owner, officers or financials with no `missing_data` / `inactive_company` / `complex_ownership` flag, or a specialist that never ran), news flags that must match news sources (adverse article without an `adverse_media` flag and the reverse, severity per policy section 5, `decline` needs a high-severity article), and merge fidelity (officers, owners, news, financials, the evaluator's risk and policy excerpts, and the bank's `conflicting_data` flags must reach the pack unchanged). Each finding names the specialist that produced it. The send-back message now also says to act on `missing` items.
- **Hypothesis:** the orchestrator is sent back to the right specialist for gaps like UK-020's missing `adverse_media` flag (when the article was retrieved), and merge mistakes by the orchestrator itself are caught and attributed to it.
- **Before / After:** pending. Rerun UK-020, then a few other companies; watch for false positives from the new checks before running the full scoring.

## 2026-10-27 · Reviewer LLM stage and prompt (`prompts/reviewer.md`, slice 3)

- **Change:** a separate reviewer session (no built-in tools; read-only `policy_search` and `bank_get_risk_appetite`; model `REVIEWER_MODEL`, default `CLAUDE_MODEL`) judges what the code stage cannot: typed flags against the evidence and the bank's risk appetite, risk factors against flags, the rationale, gaps left unflagged, and which agent caused each problem. Its prompt holds the team (generated from `SUBAGENTS`), the field-ownership table, the attribution rules and the full policy from the DB; it is given the pack, each specialist's reply, the tool-call log and the code stage's findings, and must not repeat them. A finding blocks only in the flags or policy category with evidence (a pack source id or a policy section) and a concrete contradiction; otherwise it is a warning. It runs every round on any parseable pack. If it fails or times out, the code verdict stands. `REVIEW_LLM=0` turns it off.
- **Hypothesis:** judgement errors the rules cannot see (a namesake article kept as adverse, a prohibited sector with no flag, a risk factor with no evidence) are caught and sent to the right specialist, at the cost of one extra LLM call per round (~15 to 30s).
- **Before / After:** pending. Run UK-020 and one or two harder cases (holding chain, namesake news, prohibited sector); check the `reviewer:` lines for false blocks before full scoring.

## 2026-10-27 · Orchestrator: one complete StructuredOutput call (step 6 of the system prompt)

- **Change:** step 6 of the orchestrator's inline system prompt in `agents/kyc_agent.py` now ends with: call `StructuredOutput` once, only when the whole pack is complete, never with a partial object. The debug log showed the orchestrator first submitting a stub (28 and 72 characters, missing `company` and `risk`) that the SDK refused before it submitted the real pack.
- **Hypothesis:** the refused stub call disappears from the log (one `StructuredOutput ... accepted` line per turn) and each run saves one round trip. No effect on pack content or score expected.
- **Before / After:** before, two of the three logs we looked at had a refused stub. After, first run: the stub is still there (104 chars, invented key `query_placeholder`), so the instruction does not reliably stop it on this model. Cost is one quick refused round trip; do not spend more effort on it unless it grows.

## 2026-10-27 · Reviewer prompt: `other` is a valid flag type (`prompts/reviewer.md`)

- **Change:** the first live LLM review blocked four `other` flags ("not a flag type the policy allows") because policy section 10 lists nine types and the prompt never said the pack schema also allows `other`, the catch-all the specialists are told to use for notes. The prompt now states that `other` is valid and must never be reported as an invalid type, that anything the schema already guarantees or any wording or style choice is not a finding, and that the reviewer has no register, news or bank-record tools (say so in a suggestion instead of guessing). The code stage's merge finding for a bank `conflicting_data` flag now also shows the flag's text and what the pack contains instead.
- **Hypothesis:** no blocking finding about `other` flags, so the send-back goes only to real problems and the specialists keep the notes they are required to write.
- **Before / After:** before, 4 of 5 blocking findings in the first live review were about `other`. After: pending, rerun UK-020.

## 2026-10-27 · UK-020 results so far (reviewer slices 1 to 3)

One company, one run each, so read these as observations, not a benchmark. The orchestrator and evaluator vary from run to run, and the first row also had a stale `mcp` container.

| Run | Setup | Score | Notes |
|---|---|---|---|
| 1 | placeholder-era agent team, stale `mcp` (no short-name news search), old `_check` | 74% | `adverse_news`, `risk_rating`, `recommendation` wrong; article `news-0039` never retrieved |
| 2 | after `docker compose restart mcp`, code-stage reviewer (slices 1 and 2) | not recorded | reviewer passed, one advisory |
| 3 | slice 3, but the LLM stage was blocked by its own tool guard, so code checks only | 92% | a code finding sent the pack back once; final rating `high` instead of `medium` (`risk_rating` 0%) |
| 4 | slice 3 working, `other` flags allowed in the prompt | 100% | one send-back for a code finding (bank `conflicting_data` flag not copied unchanged); round 2 passed with 3 warnings; 178s total, about $0.07 |

## 2026-10-27 · LLM reviewer findings are advisory by default (`REVIEW_LLM_BLOCKS`)

- **Change:** the LLM stage's findings no longer block or trigger a send-back unless `REVIEW_LLM_BLOCKS=1`; they are shown as warnings, with a `note` on any finding that asked to block ("kept as a warning: ..."). Code-stage findings block as before. Why: on UK-020 the code stage's severity check sent the pack back and the evaluator corrected it to the right rating (medium, 1 + 1); the LLM then blocked that correct pack in round 2 ("news-0038 fraud must be high severity, the policy has no victim exception"), the run was rejected and no pack was produced. In the previous round it had said counting that victim article as adverse is "a judgment the policy does not clearly support", and in the run before it computed a wrong total from the same article.
- **Hypothesis:** no run is failed or steered by an LLM opinion; its observations still reach the approver as warnings and suggestions. A correct pack passes if the code stage passes it.
- **Before / After:** before, 1 of 4 recent UK-020 runs ended rejected by an LLM finding on a correct pack. After: pending.

## 2026-10-27 · News values and the victim-article reading (`news`, `evaluator`, `reviewer` prompts)

- **Change:** the `news` specialist's prompt now spells out the schema's values (`sentiment` positive, neutral or adverse, never 'negative'; `severity` none, medium or high, never 'low') and states a policy reading: an article in which the company is the victim of a crime, or that is not about the company, is neutral with severity none and gets no `adverse_media` flag. The `evaluator` takes each article's sentiment and severity as the news specialist gave them (no re-classifying, adverse media counted once at the highest severity) and never states a policy reading the text does not contain. The reviewer's prompt carries the same reading, so it no longer flips on it. **This encodes an interpretation of policy section 5 (victim articles are not adverse); confirm it is the one you want.**
- **Hypothesis:** the news specialist returns valid values and `news-0038` (the fraud article on UK-020) stays neutral/none; the evaluator no longer scores it 3 points, so the rating is medium (1 + 1). Before: the specialist returned `negative`/`low`, the orchestrator translated them into "adverse, severity none", three code checks fired, and in 3 of 6 recent runs the pack still counted the victim article as high severity (rating `high`, 92%, or a run rejected).
- **Before / After:** pending.

## 2026-10-27 · Merge check tolerates translations and rewording (`review_checks.py`)

- **Change:** the news merge check compares articles by URL and only compares sentiment and severity when the specialist's values are valid, so the orchestrator is not blamed for translating `negative`/`low`; a new advisory check names the specialist for values outside the schema. A bank `conflicting_data` flag that is present with the same cited sources but other words is advisory ("reworded"); a missing or merged one still blocks.
- **Hypothesis:** the five-run streak of a send-back for the bank flag's wording ends, saving about a round (60 to 90s) per run, while a dropped conflict still blocks.
- **Before / After:** pending.

## 2026-10-27 · News/flag source linkage is a warning, presence still blocks (`review_checks.py`)

- **Change:** when an adverse article and an `adverse_media` flag both exist but the flag's cited source ids do not match the article's, the two findings ("flag cites no source that backs an article", "article not cited by any flag") are now advisory and show both sides' ids. No flag for an adverse article, or a flag with no adverse article, still blocks.
- **Hypothesis:** no more send-backs over source-id bookkeeping, since the flag and the article are both there. Observed on UK-020: two blocking findings in one round for exactly this, in a pack that otherwise looked right.
- **Before / After:** pending. The first pack on which this showed up is not saved; the next review JSON will show the two id lists.

## 2026-10-27 · Fix: the LLM review was silently skipped after a specialist reply had a schema problem

- **Change:** `run_kyc_agent` skipped the LLM stage when any finding had category `schema`, meant for "the pack itself is invalid". The advisory reply-schema findings added the same day also use that category, so a harmless warning (the bank specialist's flag severity outside low, medium or high) switched the LLM reviewer off. It now checks that the pack validates (`is_valid_pack`).
- **Observed:** a UK-020 run at 123s with no `LLM review took` line and the `Usage:` model still the orchestrator's; nothing of the reviewer's ran in either round.
- **Before / After:** pending.

## 2026-10-27 · Shared specialist rules: allowed flag severities

- **Change:** `RULES` in `kyc_agent.py` (appended to every specialist prompt) now states that a flag's `severity` is exactly `low`, `medium` or `high`, never `info` or `none`, and to use `low` for informational and `other` flags.
- **Hypothesis:** no more `bank.flags[n].severity = "info"` (informational existing-customer note) or `news.flags[n].severity = "none"` (carried over from `NewsItem.severity`, which does allow `none`). Both were the only blocking reply-schema findings on UK-001 and UK-020 and cost a send-back round (~30s, ~3 agent re-runs).
- **Before:** UK-001 round 1: bank `info` and news `none` flagged by the reply-schema check (with it temporarily blocking).
- **After:** pending. Rerun UK-001 and check that no `bank`/`news` severity findings appear.

## 2026-10-27 · Ownership specialist: officers always carry a `role`

- **Change:** step 1 of the `ownership` prompt now lists the officer fields (`name`, `role` required and as the register states it, `appointed_on`, `sources`).
- **Hypothesis:** no more `ownership.officers[n].role: Field required` in the reply-schema check. The prompt never named `role`, and the agent left it out on UK-001 and UK-020, costing a send-back round.
- **Before:** UK-001 / UK-020 round 1: ownership officers without `role` on every run.
- **After:** pending. Rerun UK-001 and check that round 1 has no `ownership` findings.

## 2026-10-27 · Human send-back: triage prompt and "revise, don't rebuild" (`reviewer_triage.md`, `REVISION_PROMPT` in `kyc_agent.py`)

- **Change:** a send-back no longer rebuilds the pack from a free-text `<feedback>` tag. The reviewer first triages the comment (`prompts/reviewer_triage.md`): each point is mapped to pack paths, a suggested owner and a status (`actionable`, `ambiguous`, `unsupported`). The orchestrator gets the raw comment, that triage as `<review_feedback>`, and the previous pack, and its system prompt gains a "Revising after a human send-back" section (only when there is a comment): re-run only the needed subagents, re-run the evaluator only if the evidence feeding risk changed, copy every other section exactly, explain unaddressed points in `notes`. The code stage then flags any section that changed without being asked for.
- **Hypothesis:** a send-back such as "the UBOs are missing" re-runs ownership (and the evaluator) and leaves company, financials and news byte-for-byte as they were, so the human's second read is only the part they asked about, and a revision is cheaper than a full rebuild.
- **Before / After:** pending. Try the three CLI scenarios in `docs/reviewer-design.md` section 13 against a saved UK-020 pack.

## 2026-10-27 · Revision: keep the comment out of the evidence, tell the reviewer it is a revision (`REVISION_PROMPT`, `<revision>` in the LLM review)

- **Change:** (1) `REVISION_PROMPT` now says to word a subagent's task as a task, never to quote the comment or triage to it, to answer the comment only in `notes`, and to drop any flag that responds to the comment. (2) The LLM review stage gets a `<revision>` note after a send-back (partial tool log is by design; a flag addressed to the reviewer is a defect). (3) Code: `requested_sections` lets `ambiguous` points change their section and keeps the regression check on when only `unsupported` points exist (it used to switch off).
- **Observed on UK-020** ("The UBOs behind the holding company are missing", no holding company exists): an `other` flag, "The reviewer's point ... is not supported by the register", replaced the earlier ownership flag; the LLM review asked to confirm provenance because only ownership ran; the regression check was off.
- **Before / After:** pending. Rerun the same command: expect no flag that mentions the reviewer, no provenance suggestion, and `notes` as the only answer.

## 2026-10-27 · Revision: take a re-run specialist's output as returned (`REVISION_PROMPT`)

- **Change:** step (c) now says that for a specialist that was re-run, its output for the fields it owns is taken exactly as returned (sentiment and severity included), and the previous pack's values are kept only for sections that were not re-run.
- **Observed on UK-020** ("re-check the supplier-of-the-year articles"): the news specialist returned the two articles as `neutral`; the orchestrator kept v1's `positive`; the merge check blocked it and a second round (about 30s, $0.25) fixed it.
- **Before / After:** pending. Rerun the same comment and expect round 1 to pass.
