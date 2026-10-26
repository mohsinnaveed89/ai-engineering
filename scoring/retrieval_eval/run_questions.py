"""Step 3: retrieve top 5 with kyc_common search, answer from those chunks, record recall@5.

Writes results.jsonl (one {"item": {...}} per question) for the promptfoo evaluation.
Gold chunk and reference answer are never shown to the answering model.

    python scoring/retrieval_eval/run_questions.py
"""
from __future__ import annotations

import sys

import anthropic
import httpx
import psycopg

from common import ANSWER_MODEL, QUESTIONS_FILE, RESULTS_FILE, read_jsonl, reply_text, words, write_jsonl
from kyc_common.RAG.ingest import OLLAMA_URL
from kyc_common.RAG.search import DATABASE_URL, search

K = 5
NEAR_DUPLICATE = 0.8     # token Jaccard above which a non-gold hit could also answer the question

SYSTEM = ("Answer the question using only the numbered context chunks. Answer in the language of the question, "
          "concisely. If the context does not contain the answer, say so.")


def preflight() -> str | None:
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=5) as conn:
            if not conn.execute("SELECT 1 FROM document_chunks LIMIT 1").fetchone():
                return "document_chunks is empty; run the ingest first."
    except psycopg.Error as e:
        return f"Postgres not reachable at {DATABASE_URL}: {e}"
    try:
        httpx.get(OLLAMA_URL, timeout=5)
    except httpx.HTTPError as e:
        return f"Ollama not reachable at {OLLAMA_URL}: {e}"
    return None


def jaccard(a: str, b: str) -> float:
    sa, sb = set(words(a)), set(words(b))
    return len(sa & sb) / max(len(sa | sb), 1)


def main() -> int:
    problem = preflight()
    if problem:
        print(problem, file=sys.stderr)
        return 1
    if not QUESTIONS_FILE.is_file():
        print(f"{QUESTIONS_FILE} missing; run generate_questions.py first.", file=sys.stderr)
        return 1

    client = anthropic.Anthropic()
    rows = []
    for row in read_jsonl(QUESTIONS_FILE):
        item = row["item"]
        hits = search(item["input"], k=K)
        gold = (item["gold_doc_id"], item["gold_chunk_idx"])
        retrieved = [(h["doc_id"], h["chunk_idx"]) for h in hits]
        gold_text = next((h["text"] for h in hits if (h["doc_id"], h["chunk_idx"]) == gold), None)
        context = "\n\n".join(f"[{i}] {h['text']}" for i, h in enumerate(hits, 1))
        msg = client.messages.create(
            model=ANSWER_MODEL, max_tokens=4000, system=SYSTEM,
            messages=[{"role": "user", "content": f"Context:\n{context}\n\nQuestion: {item['input']}"}])
        item.update({
            "model_answer": reply_text(msg),
            "retrieved": [f"{d}#{i}" for d, i in retrieved],
            "recall_hit": gold in retrieved,
            # other top-5 chunks that nearly duplicate the gold chunk: review these questions by hand
            "ambiguous_with": [f"{h['doc_id']}#{h['chunk_idx']}" for h in hits
                               if gold_text and (h["doc_id"], h["chunk_idx"]) != gold
                               and jaccard(h["text"], gold_text) > NEAR_DUPLICATE],
        })
        rows.append(row)
        print(f"{'HIT ' if item['recall_hit'] else 'MISS'} {item['language']} {item['input'][:70]}")

    write_jsonl(RESULTS_FILE, rows)
    hits = sum(r["item"]["recall_hit"] for r in rows)
    print(f"recall@{K}: {hits}/{len(rows)} = {hits / len(rows):.2f}  -> {RESULTS_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
