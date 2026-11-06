-- Manufacturing RAG Database Schema
-- PostgreSQL 16 + pgvector

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- ─────────────────────────────────────────────
-- Documents Table
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS documents (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  filename        TEXT NOT NULL,
  original_name   TEXT NOT NULL,
  category        TEXT NOT NULL CHECK (category IN (
                    'DRAWING_2D','INSPECTION_FIR','FINANCIAL_PO_INVOICE',
                    'STORES_FG','TOOLING_MATERIAL','GENERAL')),
  mime_type       TEXT,
  file_size       BIGINT,
  storage_path    TEXT NOT NULL,
  storage_bucket  TEXT NOT NULL,
  page_count      INT,
  status          TEXT DEFAULT 'pending' CHECK (status IN ('pending','processing','indexed','error')),
  error_message   TEXT,

  -- ── Structured metadata fields (optional, user-supplied + auto-extracted) ──
  doc_title       TEXT,                     -- Human-readable title
  description     TEXT,                     -- Free-text description
  part_name       TEXT,                     -- Part / assembly name
  part_number     TEXT,                     -- Exact part number e.g. DWG-A901
  revision        TEXT,                     -- Revision code e.g. REV3, B, 02
  developed_date  DATE,                     -- Date drawn / developed
  customer_name   TEXT,                     -- Customer / client name
  issue_details   TEXT,                     -- Known issues / NCRs / deviations
  auto_extracted  JSONB DEFAULT '{}',       -- Fields extracted via LLM/regex from content

  metadata        JSONB DEFAULT '{}',       -- Catch-all for future extensibility
  source_path     TEXT,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────
-- Chunks Table (Dense + Sparse Indexing)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chunks (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id   UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_index   INT NOT NULL,
  content       TEXT NOT NULL,
  content_type  TEXT DEFAULT 'text' CHECK (content_type IN ('text','table','image_caption','ocr','metadata_header')),
  page_number   INT,
  sheet_name    TEXT,
  bounding_box  JSONB,
  embedding     vector(1024),
  sparse_vector JSONB,
  bm25_tokens   TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', unaccent(content))) STORED,
  metadata      JSONB DEFAULT '{}',
  created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────
-- Indexes
-- ─────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_chunks_document_id   ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding      ON chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_chunks_bm25           ON chunks USING gin(bm25_tokens);
CREATE INDEX IF NOT EXISTS idx_chunks_content_trgm   ON chunks USING gin(content gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_chunks_content_type   ON chunks(content_type);

CREATE INDEX IF NOT EXISTS idx_documents_category    ON documents(category);
CREATE INDEX IF NOT EXISTS idx_documents_status      ON documents(status);
CREATE INDEX IF NOT EXISTS idx_documents_part_number ON documents(part_number);   -- exact match filter
CREATE INDEX IF NOT EXISTS idx_documents_revision    ON documents(revision);
CREATE INDEX IF NOT EXISTS idx_documents_part_name   ON documents USING gin(to_tsvector('english', unaccent(COALESCE(part_name,''))));
CREATE INDEX IF NOT EXISTS idx_documents_metadata    ON documents USING gin(metadata);
CREATE INDEX IF NOT EXISTS idx_documents_auto_extracted ON documents USING gin(auto_extracted);

-- ─────────────────────────────────────────────
-- Auto-update updated_at
-- ─────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER documents_updated_at
  BEFORE UPDATE ON documents
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ─────────────────────────────────────────────
-- Hybrid Search RRF Function
-- Supports optional pre-filter by part_number and category
-- ─────────────────────────────────────────────
CREATE OR REPLACE FUNCTION hybrid_search(
  query_embedding   vector(1024),
  query_text        TEXT,
  match_count       INT DEFAULT 20,
  rrf_k             INT DEFAULT 60,
  filter_category   TEXT DEFAULT NULL,
  filter_part_number TEXT DEFAULT NULL
)
RETURNS TABLE (
  chunk_id     UUID,
  document_id  UUID,
  content      TEXT,
  page_number  INT,
  content_type TEXT,
  rrf_score    FLOAT,
  dense_rank   INT,
  sparse_rank  INT
) AS $$
WITH filtered_chunks AS (
  -- Pre-filter by document metadata before expensive vector ops
  SELECT c.id, c.document_id, c.content, c.page_number, c.content_type, c.embedding, c.bm25_tokens
  FROM chunks c
  JOIN documents d ON d.id = c.document_id
  WHERE
    (filter_category IS NULL   OR d.category    = filter_category)
    AND (filter_part_number IS NULL OR d.part_number = filter_part_number)
    AND c.embedding IS NOT NULL
),
dense AS (
  SELECT id, document_id, content, page_number, content_type,
         ROW_NUMBER() OVER (ORDER BY embedding <=> query_embedding) AS rank
  FROM filtered_chunks
  ORDER BY embedding <=> query_embedding
  LIMIT match_count * 2
),
sparse AS (
  SELECT id, document_id, content, page_number, content_type,
         ROW_NUMBER() OVER (ORDER BY ts_rank_cd(bm25_tokens, query) DESC) AS rank
  FROM filtered_chunks,
       plainto_tsquery('english', unaccent(query_text)) AS query
  WHERE bm25_tokens @@ query
  ORDER BY ts_rank_cd(bm25_tokens, query) DESC
  LIMIT match_count * 2
),
fused AS (
  SELECT
    COALESCE(d.id, s.id) AS id,
    COALESCE(d.document_id, s.document_id) AS document_id,
    COALESCE(d.content, s.content) AS content,
    COALESCE(d.page_number, s.page_number) AS page_number,
    COALESCE(d.content_type, s.content_type) AS content_type,
    COALESCE(1.0/(rrf_k + d.rank), 0) + COALESCE(1.0/(rrf_k + s.rank), 0) AS rrf_score,
    COALESCE(d.rank::INT, 9999) AS dense_rank,
    COALESCE(s.rank::INT, 9999) AS sparse_rank
  FROM dense d FULL OUTER JOIN sparse s ON d.id = s.id
)
SELECT id AS chunk_id, document_id, content, page_number, content_type,
       rrf_score, dense_rank, sparse_rank
FROM fused
ORDER BY rrf_score DESC
LIMIT match_count;
$$ LANGUAGE sql;
