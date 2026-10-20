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
