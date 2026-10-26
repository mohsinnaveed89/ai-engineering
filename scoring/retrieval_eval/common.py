"""Shared paths, models and helpers for the mini retrieval eval."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
KIT_ROOT = HERE.parent.parent
QUESTIONS_FILE = HERE / "questions.jsonl"      # frozen dataset
RESULTS_FILE = HERE / "results.jsonl"          # questions + retrieval + model answers

GENERATOR_MODEL = "claude-opus-5-5"
ANSWER_MODEL = "claude-haiku-5-5"
JUDGE_MODEL = "claude-opus-5-5"                # keep in sync with promptfooconfig.yaml

load_dotenv(KIT_ROOT / ".env")
sys.path.insert(0, str(KIT_ROOT / "packages" / "kyc_common"))   # works without pip install -e


def reply_text(msg) -> str:
    """Concatenate the text blocks of a reply; models may lead with a thinking block."""
    return "".join(b.text for b in msg.content if b.type == "text")


def words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def ngrams(text: str, n: int = 3) -> set[tuple[str, ...]]:
    w = words(text)
    return {tuple(w[i:i + n]) for i in range(len(w) - n + 1)}


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
