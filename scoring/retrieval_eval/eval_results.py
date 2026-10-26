"""Step 4: evaluate results.jsonl with Promptfoo (recall@5 + answer alignment).

    python scoring/retrieval_eval/eval_results.py
    npx promptfoo@0.121.18 view        # item-level results

Environment override: RAG_EVAL_DATA_FILE (alternate results JSONL).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from common import RESULTS_FILE, read_jsonl

HERE = Path(__file__).resolve().parent
PROMPTFOO_CONFIG = HERE / "promptfooconfig.yaml"
PROMPTFOO_PACKAGE = "promptfoo@0.121.18"


def _promptfoo_result_issues(payload: Any) -> list[str]:
    """Operational errors only; ordinary failed assertions are not errors."""
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, dict):
        return ["Promptfoo wrote results in an unexpected format."]
    issues: list[str] = []
    stats = results.get("stats")
    if isinstance(stats, dict) and isinstance(stats.get("errors"), int) and stats["errors"]:
        issues.append(f"Promptfoo reported {stats['errors']} evaluation error(s).")

    def inspect(grading: Any) -> None:
        if not isinstance(grading, dict):
            return
        meta = grading.get("metadata")
        if isinstance(meta, dict) and meta.get("graderError") is True:
            issues.append(str(grading.get("reason") or "A model grader failed."))
        for part in grading.get("componentResults") or []:
            inspect(part)

    for row in results.get("results") or []:
        if not isinstance(row, dict):
            continue
        reason = row.get("failureReason")      # 0.121.18: ASSERT=1, ERROR=2
        if reason == 2:
            issues.append(str(row.get("error") or "Promptfoo reported an evaluation error."))
        elif row.get("error") and not (type(reason) is int and reason == 1):
            issues.append(str(row["error"]))
        response = row.get("response")
        if isinstance(response, dict) and response.get("error"):
            issues.append(str(response["error"]))
        inspect(row.get("gradingResult"))
    return list(dict.fromkeys(issues))


def print_recall(data_file: Path) -> None:
    by_language: dict[str, list[bool]] = defaultdict(list)
    for row in read_jsonl(data_file):
        by_language[row["item"]["language"]].append(bool(row["item"]["recall_hit"]))
    every = [hit for hits in by_language.values() for hit in hits]
    print(f"recall@5 overall: {sum(every)}/{len(every)} = {sum(every) / len(every):.2f}")
    for language, hits in sorted(by_language.items()):
        print(f"  {language}: {sum(hits)}/{len(hits)} = {sum(hits) / len(hits):.2f}")


def main() -> int:
    data_file = Path(os.getenv("RAG_EVAL_DATA_FILE") or RESULTS_FILE).expanduser().resolve()
    if not data_file.is_file():
        print(f"Results file not found: {data_file} (run run_questions.py first)", file=sys.stderr)
        return 1
    npx = shutil.which("npx")
    if npx is None:
        print("Promptfoo requires Node.js (npx not found).", file=sys.stderr)
        return 1

    print_recall(data_file)
    environment = os.environ.copy()
    environment["RAG_EVAL_DATA_FILE"] = str(data_file)
    environment["PROMPTFOO_PASS_RATE_THRESHOLD"] = "0"   # recall misses are expected data, not a failed run
    environment["PROMPTFOO_DISABLE_TELEMETRY"] = "1"
    with tempfile.TemporaryDirectory(prefix="retrieval-eval-promptfoo-") as tmp:
        output_path = Path(tmp) / "results.json"
        result = subprocess.run(
            [npx, "--yes", PROMPTFOO_PACKAGE, "eval", "-c", str(PROMPTFOO_CONFIG), "--no-cache",
             "--output", str(output_path)],
            cwd=HERE, env=environment, check=False)
        if result.returncode == 0:
            try:
                payload = json.loads(output_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                print(f"Could not read Promptfoo results: {error}", file=sys.stderr)
                return 1
            issues = _promptfoo_result_issues(payload)
            if issues:
                print("Promptfoo completed, but the evaluation hit an operational error:", file=sys.stderr)
                for issue in issues:
                    print(f"- {issue}", file=sys.stderr)
                return 1
    if result.returncode == 0:
        print(f"Review item-level results with: npx {PROMPTFOO_PACKAGE} view", flush=True)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
