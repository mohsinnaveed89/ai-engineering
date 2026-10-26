"""Generate the frozen question set (run once).

5 English (3 policy, 2 annual report) + 5 Portuguese (annual reports). Each question is a strict
paraphrase of one chunk, so BM25 cannot match it on shared wording; its gold answer is that chunk's
(doc_id, chunk_idx). Writes questions.jsonl; refuses to overwrite it without --regenerate.

    python scoring/retrieval_eval/generate_questions.py [--regenerate] [--seed 7]
"""
from __future__ import annotations

import argparse
import json
import random
import sys

import anthropic
import psycopg

from common import GENERATOR_MODEL, QUESTIONS_FILE, ngrams, reply_text, write_jsonl
from kyc_common.RAG.search import DATABASE_URL

PLAN = [("en", "policy", 3), ("en", "annual_report", 2), ("pt", "annual_report", 5)]
MAX_COPIED_TRIGRAMS = 0      # a question may not share any 3-word phrase with its chunk
MAX_DIGIT_SHARE = 0.25       # skip mostly-numeric chunks (tables)
MIN_CHARS = 400

PROMPT = """You write test questions for a retrieval evaluation. Read the document chunk and write ONE question
that this chunk (and no other chunk) answers, plus the answer.

Rules:
- Write in {language}.
- Paraphrase strictly: use synonyms or descriptions instead of the chunk's own distinctive terms, and never
  copy a phrase of three or more consecutive words from the chunk.
- Keep proper names (company, regulation, product) and years exactly as written. {scope}
- The answer must be fully supported by the chunk and be short (1-3 sentences), in {language}.

Chunk metadata: {meta}
Chunk:
\"\"\"
{text}
\"\"\"

Reply with JSON only: {{"question": "...", "answer": "..."}}"""


def candidates(conn, language: str, doc_type: str) -> list[tuple]:
    company_filter = "IS NULL" if doc_type == "policy" else "IS NOT NULL"
    rows = conn.execute(
        f"SELECT doc_id, chunk_idx, text, metadata FROM document_chunks "
        f"WHERE metadata->>'language' = %s AND (metadata->>'company') {company_filter} "
        f"AND metadata->>'section' <> 'Cover'", (language,)).fetchall()
    keep = []
    for doc_id, idx, text, meta in rows:
        digits = sum(c.isdigit() for c in text) / max(len(text), 1)
        if len(text) >= MIN_CHARS and digits <= MAX_DIGIT_SHARE:
            keep.append((doc_id, idx, text, meta))
    return keep


def ask(client, language: str, doc_type: str, text: str, meta: dict) -> dict:
    scope = ("Name the company and year in the question so only this report matches."
             if doc_type == "annual_report" else "Make the topic specific enough that only this section matches.")
    shown = {k: meta.get(k) for k in ("company", "country", "year", "section")}
    msg = client.messages.create(
        model=GENERATOR_MODEL, max_tokens=4000,
        messages=[{"role": "user", "content": PROMPT.format(
            language="English" if language == "en" else "Brazilian Portuguese",
            scope=scope, meta=json.dumps(shown, ensure_ascii=False), text=text)}])
    raw = reply_text(msg)
    return json.loads(raw[raw.index("{"):raw.rindex("}") + 1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--regenerate", action="store_true", help="overwrite the frozen question set")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    if QUESTIONS_FILE.exists() and not args.regenerate:
        print(f"{QUESTIONS_FILE} exists; the set is frozen. Pass --regenerate to replace it.", file=sys.stderr)
        return 1

    rng = random.Random(args.seed)
    client = anthropic.Anthropic()
    out, used_docs = [], set()
    with psycopg.connect(DATABASE_URL) as conn:
        for language, doc_type, count in PLAN:
            pool = candidates(conn, language, doc_type)
            rng.shuffle(pool)
            made = 0
            for doc_id, idx, text, meta in pool:
                if made == count:
                    break
                if doc_type == "annual_report" and doc_id in used_docs:   # one report question per report
                    continue
                try:
                    qa = ask(client, language, doc_type, text, meta)
                except (ValueError, KeyError, json.JSONDecodeError):
                    continue
                if len(ngrams(qa["question"]) & ngrams(text)) > MAX_COPIED_TRIGRAMS:
                    continue                        # leaked wording: skip this chunk
                used_docs.add(doc_id)       # policies are few, so several chunks per policy are fine
                made += 1
                out.append({"item": {
                    "input": qa["question"], "expected_answer": qa["answer"],
                    "gold_doc_id": doc_id, "gold_chunk_idx": idx,
                    "language": language, "doc_type": doc_type,
                    "section": meta.get("section"), "company": meta.get("company"), "year": meta.get("year")}})
                print(f"[{language}/{doc_type}] {qa['question']}")
            if made < count:
                print(f"only {made}/{count} questions for {language}/{doc_type}", file=sys.stderr)
                return 1
    write_jsonl(QUESTIONS_FILE, out)
    print(f"wrote {len(out)} questions to {QUESTIONS_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
