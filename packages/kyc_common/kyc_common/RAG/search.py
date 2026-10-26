import os, re, sys
from collections import Counter
import psycopg
from rank_bm25 import BM25Okapi
from .ingest import embed, to_pg

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://academy:academy@localhost:5432/academy")

def tokens(s):
    return re.findall(r"\w+", s.lower())

def vector_rank(conn, question, doc_id=None):
    q = to_pg(embed([question])[0])                     # same model as ingest
    rows = conn.execute("SELECT doc_id, chunk_idx FROM document_chunks WHERE (%s::text IS NULL OR doc_id = %s) "
                        "ORDER BY embedding <=> %s::vector", (doc_id, doc_id, q))
    return [r for r in rows]                            # closest first

def bm25_rank(rows, question):
    # index the header too (as ingest does for the embedding), so a company name matches a chunk that never says it
    docs = [tokens(f"{m.get('company') or ''} {m.get('year') or ''} {m.get('section') or ''} {text}") for _, _, text, m in rows]
    scores = BM25Okapi(docs).get_scores(tokens(question))
    order = sorted(range(len(rows)), key=lambda i: scores[i], reverse=True)
    return [rows[i][:2] for i in order]                 # best match first

def search(question, k=5, rrf_k=60, doc_id=None):
    """Top-k chunks as dicts: doc_id, chunk_idx, text, metadata (company, country, year, section, ...), score.
    `doc_id` restricts the search to one document (e.g. 'kyc_policy')."""
    with psycopg.connect(DATABASE_URL) as conn:
        rows = conn.execute("SELECT doc_id, chunk_idx, text, metadata FROM document_chunks "
                            "WHERE (%s::text IS NULL OR doc_id = %s)", (doc_id, doc_id)).fetchall()
        if not rows:
            return []
        rankings = [vector_rank(conn, question, doc_id), bm25_rank(rows, question)]
    fused = Counter()
    for ranking in rankings:
        for rank, key in enumerate(ranking, 1):
            fused[key] += 1 / (rrf_k + rank)            # reciprocal rank fusion
    chunk = {(d, i): (t, m) for d, i, t, m in rows}
    return [{"doc_id": d, "chunk_idx": i, "text": chunk[d, i][0], "metadata": chunk[d, i][1], "score": s}
            for (d, i), s in fused.most_common(k)]

if __name__ == "__main__":
    for hit in search(" ".join(sys.argv[1:])):
        print(f"{hit['score']:.4f}  {hit['doc_id']}#{hit['chunk_idx']}  {hit['text'][:80]!r}")
