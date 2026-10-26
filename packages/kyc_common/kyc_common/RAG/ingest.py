import json, os
import httpx, psycopg
from .chunks import index, chunk_document

BATCH = 32
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://academy:academy@localhost:5432/academy")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

def embed(texts):
    r = httpx.post(f"{OLLAMA_URL}/api/embed", json={"model": "bge-m3", "input": texts}, timeout=120)
    r.raise_for_status()
    return r.json()["embeddings"]                       # list of 1024-float lists

def to_pg(vec):
    return "[" + ",".join(map(str, vec)) + "]"          # pgvector text format

def embed_input(c):
    # lesson 3: a bare chunk is ambiguous, so say whose and when
    return f"{c['company'] or 'KYC policy'} | {c['year']} | {c['section']}\n{c['text']}"

def main():
    rows = [(i, c) for p in index() for i, c in enumerate(chunk_document(p))]
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        for s in range(0, len(rows), BATCH):
            batch = rows[s:s + BATCH]
            vecs = embed([embed_input(c) for _, c in batch])
            for (i, c), v in zip(batch, vecs):
                assert len(v) == 1024, len(v)            # fail loudly if the model changes
                meta = {k: c[k] for k in c if k != "text"}
                conn.execute(
                    """INSERT INTO document_chunks (doc_id, chunk_idx, text, metadata, embedding)
                       VALUES (%s, %s, %s, %s, %s::vector)
                       ON CONFLICT (doc_id, chunk_idx) DO UPDATE
                       SET text = EXCLUDED.text, metadata = EXCLUDED.metadata, embedding = EXCLUDED.embedding""",
                    (c["doc_id"], i, c["text"], json.dumps(meta), to_pg(v)))
            print(f"{min(s + BATCH, len(rows))}/{len(rows)}")

if __name__ == "__main__":
    main()