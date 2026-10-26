"""Adapt results.jsonl into Promptfoo test cases."""
from __future__ import annotations

import json
import os
from pathlib import Path


def generate_tests(config=None):
    config = config or {}
    dataset = Path(os.getenv("RAG_EVAL_DATA_FILE", config.get("dataset", "results.jsonl"))).expanduser()
    if not dataset.is_absolute():
        dataset = (Path(__file__).parent / dataset).resolve()
    tests = []
    with dataset.open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            item = json.loads(line)["item"]
            tests.append({
                "description": item["input"],
                "vars": {"item": {**item, "recall_hit": 1 if item["recall_hit"] else 0}},
                "metadata": {"language": item["language"], "doc_type": item["doc_type"]},
            })
    return tests
