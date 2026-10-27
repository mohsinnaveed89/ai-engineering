# KYC pack rejected by the policy citation check: problems found

Run: `python -m app.cli --company-id UK-020` (Blackwater Harbour Media). Pipeline: orchestrator, then ownership / news / bank / financials subagents, then the `evaluator` subagent, then a final `StructuredOutput` pack checked by `_check()`.

## Symptom

- The pack was rejected with `'eval-policy-8' excerpt is not quoted verbatim from the text of 'kyc_policy.md#8'`.
- The agent was asked to fix it once, failed again, and returned `None`.
- The evaluator appeared to run 2 to 3 times, and the CLI crashed with a pydantic error.

## Problems

| # | Problem | Where | Status |
|---|---|---|---|
| 1 | The check was too literal about markdown. The model dropped bold markers, so `**conflicting data**` became `conflicting data`, and a faithful quote failed (`eval-policy-3`). | `citation_problems()` Check 4, [risk_check.py](../services/api/app/agents/risk_check.py) | Fixed |
| 2 | The excerpt joined two places in the section with `...` (a table row and the `Rating:` line) and also dropped bold (`eval-policy-8`). | Evaluator and orchestrator quoting | Fixed in the validator and prompts. Not rerun end to end. |
| 3 | On rejection the orchestrator tried `policy_search`, which `_orchestrator_tool_guard` denies. The rejection message implied it should search. | [kyc_agent.py](../services/api/app/agents/kyc_agent.py) | Fixed: message and orchestrator step 5 now say it cannot search |
| 4 | The orchestrator re-ran the evaluator in response (about 30s extra, 2 to 3 evaluator runs). The evaluator was only called once in the first pass. | Retry behaviour after the rejection | Expected to go away once problems 1 to 3 are fixed. Not verified. |
| 5 | The CLI crashed on `KycPack.model_validate(None)`, which hides the real error ("did not produce a valid pack"). | [cli.py:115](../services/api/app/cli.py#L115) | **Open** |
| 6 | The first debug readout `_first_divergence` compares from index 0, so it is misleading when an excerpt comes from the middle of a section. | [kyc_agent.py](../services/api/app/agents/kyc_agent.py) | **Open**. Use the `/tmp/kyc_debug_attempt*.json` dump instead. |

## What was ruled out

- Retrieval: the evaluator retrieved sections 2 to 9, including #3 and #8.
- Overwriting: section 8 came back twice with identical text (459 chars).
- The dump shows only the final pack, so it cannot show whether the evaluator or the orchestrator altered the excerpts. Both prompts were changed.

## Changes made

1. **Check 4** now uses word overlap: it passes when at least 80% of the excerpt's words appear in the section text (`MIN_WORD_OVERLAP`). Case, markdown, punctuation, order and `...` are ignored. Earlier in the session it was an in-order, `...`-aware match; the user asked for something simpler.
2. **Evaluator prompt:** quote one contiguous span (a sentence or one table row), never join with `...`.
3. **Orchestrator step 5:** add the evaluator's policy sources unchanged, never retype them, and never call `policy_search`.
4. **Rejection message:** says the word-overlap threshold and tells the orchestrator not to search.
5. **Debug logging** in `kyc_agent.py`: logs each `policy_search` call (who, query, section returned), and on a failed check shows each cited excerpt against the section text and writes `/tmp/kyc_debug_attempt<N>.json` inside the container.
6. A `CHANGELOG.md` entry was added in `services/api/app/prompts/`. Its before/after scores are pending. It still describes the earlier in-order check, so update it to match the word-overlap rule.

## Trade-off to know about

Check 4 no longer proves an excerpt is a quote. A rewritten sentence that reuses the policy's words can pass, and so can a wrong rule built from real words (for example `Conflicting data | 1 points`). The points arithmetic and section 8 citation checks still run. Raising `MIN_WORD_OVERLAP` (for example to 0.9) makes it stricter.

## Next steps

- Rerun UK-020. Success is no `Pack invalid` line, one `StructuredOutput`, and no evaluator call after about 40s.
- Fix problem 5 (check for `None` before `model_validate`).
- Decide whether to keep or remove the debug logging.
- Update the CHANGELOG entry and fill in the before/after scores.
