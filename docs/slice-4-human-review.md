# Slice 4: human send-back review

How a human's send-back comment is handled, where it lives in the code, and how to test it. The design is in [reviewer-design.md](reviewer-design.md) (sections 6 and 7).

## What it does

Before slice 4, a send-back was a free-text `<feedback>` tag on a full rebuild. Nothing mapped the comment to a part of the pack, and nothing stopped a rebuild from changing parts the human had already accepted. Now:

```
 human: "Send back" + comment
        │
        ▼
 1. TRIAGE   reviewer reads the comment + previous pack, returns points:
             each point → pack paths, suggested owner, status (actionable | ambiguous | unsupported)
             = a ReviewFeedback with trigger human_feedback, verdict revise
        │
        ▼
 2. REVISE   orchestrator (new session) gets <feedback>, <review_feedback>, <previous_pack>
             re-runs only the subagents it needs; copies every other section from the previous pack
        │
        ▼
 3. VERIFY   the normal review loop (code stage + LLM stage) on the new pack, plus the
             unrequested-change check: did anything change that nobody asked for?
             blocking finding → sent back to the orchestrator (up to REVIEW_MAX_SENDBACKS)
```

**Unrequested change** = a section of the new pack that differs from the previous pack although the comment did not ask for it. Which sections may change comes from the triage: the paths of actionable and ambiguous points. `unsupported` points request nothing. If triage fails, or a point names no path, nothing can be judged and the check is off.

| Section that changed without being requested | Result |
|---|---|
| `company`, `officers`, `owners`, `financial_summary` | blocking: sent back to restore it |
| `news`, `flags`, `risk` | advisory: a warning for the approver |
| `sources`, source-id lists, list order | ignored |

`risk` may change when `company`, `owners`, `news`, `flags` or `financial_summary` changed, since they feed the score. Paths named by earlier send-back rounds of the same run also count as requested.

## Where it is in the code

| What | Where |
|---|---|
| Entry: the API's send-back starts version N+1 with `ctx.feedback`, `ctx.previous_pack`, `ctx.review_history` | `runs.py::review` and `start` (unchanged) |
| The loop: triage first, then the turn, then review rounds | `agents/kyc_agent.py::run_kyc_agent`, `_triage` |
| First message of a revision: `<feedback>`, `<review_feedback>`, `<previous_pack>` | `kyc_agent.py::build_user_message` |
| "Revising after a human send-back" section, added to the system prompt only when there is a comment | `kyc_agent.py::REVISION_PROMPT` |
| Triage: models, prompt builder, LLM call, conversion to `ReviewFeedback`, `requested_sections` | `agents/reviewer_triage.py`, `prompts/reviewer_triage.md` |
| The unrequested-change check | `agents/review_checks.py::unrequested_changes` |
| The check joins the code stage | `agents/reviewer.py::review_output` (new arguments `previous_pack`, `requested`) |
| The LLM review is told it is reviewing a revision | `agents/reviewer_llm.py::revision_note` |

### How it plugs into `reviewer.py`

`reviewer.py` already held the feedback models from slice 1, and slice 4 reuses them rather than adding new ones:

- `ReviewFeedback.trigger = "human_feedback"` and `verdict = "revise"` are the triage result. `Finding` already had `origin: human`, `category: human_feedback`, `reviewer_comment` and `still_unresolved`.
- `review_output` (the code stage) takes two new optional arguments. With `previous_pack` set it also calls `unrequested_changes(previous, pack, requested)`; its `Problem`s become ordinary findings (category `merge`, rule "human send-back: change only what was asked"), so they flow through the same blocking, send-back and `PackRejected` path as every other finding. No second loop.
- `reviewer_triage.py` is a separate module because triage is a different job (map a comment) with its own prompt and output model, reusing the reviewer's session builder (`build_reviewer_options`) and never raising.

## Testing

### Unit tests (no LLM, no docker, under a second)

```bash
source .venv/bin/activate
pytest tests                          # whole suite
pytest tests/test_review_human.py     # slice 4 only (25 tests)
```

Expected: everything passes except 3 tests that were already failing before slice 4 (`test_a_specialist_reply_with_values_outside_the_schema_…`, `test_reply_schema_problems_reach_the_feedback_…`, `test_the_llm_stage_still_runs_when_a_specialist_reply_had_schema_problems`). They come from a `# TEMP blocking=True` in `review_checks.py::reply_schema_findings`.

### Live runs (call the real API, about $0.20 to $0.50 each)

```bash
docker compose up -d
docker compose restart mcp            # only if services/mcp/server.py changed

# 1. a first pack to send back (saved as /tmp/v1.json inside the api container)
docker compose exec api python -m app.cli --company-id UK-020 --out /tmp/v1.json

# 2. send it back with a comment (swap the --feedback text per scenario below)
docker compose exec api python -m app.cli --company-id UK-020 \
  --previous /tmp/v1.json \
  --feedback "The two supplier-of-the-year articles look like they are about a different company. Please re-check them and remove any that are not about this company." \
  --out /tmp/v2.json

# 3. which sections changed?
docker compose exec -T api python - <<'EOF'
import json
a, b = json.load(open('/tmp/v1.json')), json.load(open('/tmp/v2.json'))
for k in a:
    if k != "generated_at": print(f"{k:18}", "same" if a[k] == b.get(k) else "DIFFERENT")
EOF
```

The reviewer's feedback for every round (triage first) is written to `/tmp/review-feedback.json` in the container; change it with `--review-out`. Use a different `--out` per scenario so `/tmp/v1.json` is never overwritten.

### Scenarios and what to expect (UK-020)

| Comment | Triage | Should change | Must not change |
|---|---|---|---|
| "The UBOs behind the holding company are missing" (no holding company exists) | one `ambiguous` point, `owners`, owner `ownership` | only `notes` (explains the register shows none) | everything else; no invented owner |
| "The two supplier-of-the-year articles look like they are about a different company…" | `actionable`, `news[0]` and `news[2]`, owner `news` | `news` (the articles' sentiment, or removal if the agent can show they are not this company), `notes` | `company`, `officers`, `owners`, `financial_summary`, `risk`, `flags` |
| "Make it low risk" | `unsupported`, `risk.rating` | nothing (maybe `notes`) | everything |
| A comment that names no part of the pack | point with no path | whatever the agent finds | nothing is policed (check is off) |

### What to look for in the console

- `reviewer: Triage took Ns: N point(s)` followed by one line per point: `actionable | ambiguous | unsupported [owner] path: request`.
- Only the subagents that the comment needs are called (`ownership`, `news`, ...), not all five.
- `Round 1: pass`. A `fail` with a blocking finding "changed but nothing in the human's comment asked for it" is the unrequested-change check working: the pack is sent back to restore that section.
- `Triage unavailable (...)`: triage failed. The comment still goes through, but the unrequested-change check is off for that run.
- Problems: an invented owner or company; a flag that mentions the reviewer, the comment or the send-back (the answer belongs in `notes`); a section changing that the comment did not ask for and the run still passing.

### Through the web app

Open http://localhost:3000, run UK-020, then **Send back** with the same comment. The new version appears as v2 and the earlier pack stays in `pack_history`. The triage is stored with the run's events.

## Known gaps

- The merge check compares the final pack with the specialists' fresh replies. In a revision the previous pack's ids and flag wording are kept on purpose, so the reviewer can warn about a "difference" that is intended (advisory only). Making that check revision-aware is the next step.
- Triage only runs with `REVIEW_LLM` on.
- `news`, `flags` and `risk` unrequested changes are advisory until real runs show it is safe to block.
- Scoring (`--run-all --compare`) has not been run for slice 4; the live runs above are single-company checks.
