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

- **Change:** debug logging showed the pack was rejected twice because `eval-policy-3` / `eval-policy-8` excerpts dropped markdown bold (`**conflicting data**`) and `eval-policy-8` joined a table row to the `Rating:` line with `...`. The orchestrator then tried `policy_search` (denied by the tool guard) and re-ran the evaluator. `risk_check._quoted_in` now ignores `*` and backticks and accepts `...` / `…` between pieces that appear in order (still no paraphrase). The `evaluator` prompt asks for one contiguous span per source (one row or sentence), the orchestrator must add the evaluator's policy sources unchanged and may not search, and the rejection message says so.
- **Hypothesis:** the first `StructuredOutput` validates, so the fix round and the extra evaluator runs disappear (UK-020 latency drops by ~30s) with no change in risk scores.
- **Before / After:** pending. Rerun UK-020 and confirm no `Pack invalid` line.
