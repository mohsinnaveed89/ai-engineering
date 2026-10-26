-- Runs on first start of the `db` container (empty volume only).
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS runs (
    id             UUID PRIMARY KEY,
    company_name   TEXT        NOT NULL,
    country        TEXT        NOT NULL CHECK (country IN ('UK', 'PT', 'BR')),
    status         TEXT        NOT NULL,              -- queued | running | awaiting_review | sent_back | approved | declined | failed
    version        INT         NOT NULL DEFAULT 1,    -- +1 every time the human sends the pack back
    pack           JSONB,                             -- latest KYC pack
    pack_history   JSONB       NOT NULL DEFAULT '[]', -- previous versions
    events         JSONB       NOT NULL DEFAULT '[]', -- progress events
    review_status  TEXT        NOT NULL DEFAULT 'pending',
    reviews        JSONB       NOT NULL DEFAULT '[]',
    error          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS runs_created_at_idx ON runs (created_at DESC);
CREATE INDEX IF NOT EXISTS runs_company_idx ON runs (company_name, country);

-- Chunks of the annual reports and policies, filled by `python -m kyc_common.RAG.ingest` (bge-m3, 1024 dimensions).
CREATE TABLE IF NOT EXISTS document_chunks (
    id         BIGSERIAL PRIMARY KEY,
    doc_id     TEXT  NOT NULL,
    chunk_idx  INT   NOT NULL,
    text       TEXT  NOT NULL,
    metadata   JSONB NOT NULL,
    embedding  vector(1024) NOT NULL,
    UNIQUE (doc_id, chunk_idx)
);
