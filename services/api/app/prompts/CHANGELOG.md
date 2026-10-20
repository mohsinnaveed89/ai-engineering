# Prompt changelog

One entry per prompt change: what changed, why, and the score before and after.

## 2026-10-19 · Day 1: identity extraction with Claude

- **Change:** `orchestrator.md` system prompt (role + the three pack rules). New identity-extraction user prompt in `pipeline.py::extract_identity`: copy legal name, registration number, status and address verbatim from `<registry_record>`, join split addresses with ", ", null when not stated. Structured output (JSON schema from the `CompanyIdentity` Pydantic model), validated with Pydantic, one retry with the validation error fed back.
- **Hypothesis:** Identity fields stay at or near the baseline (they were already copied by code), and the missing-address cases improve because the model returns null instead of an empty or partial address. The rest of the pack is unchanged (still the placeholder).
- **Before:** 66.0% overall (`report-20261019-161453-baseline`); legal_name 99.3%, registration_number 98.7%, status 98.7%, address 98.7%.
- **After:** spot check only, full run pending. UK-001 69%, PT-001 69%, BR-001 62%, with all four identity fields at 100% and sourcing OK. About $0.005 and 3 s per company on claude-sonnet-5-5.
