-- Codebase Archaeologist schema (phase 1: repository ingestion + RAG).
--
-- `chunks` is deliberately source-agnostic: code and docs today, and in later
-- phases commits, pull requests and issues become additional `source_type`s
-- that flow through the same retrieval path. Relationships between them will
-- live in a separate graph layer (entities/edges) rather than in this table.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS repositories (
    id              BIGSERIAL PRIMARY KEY,
    url             TEXT NOT NULL UNIQUE,
    owner           TEXT,
    name            TEXT NOT NULL,
    local_path      TEXT,
    default_branch  TEXT,
    head_sha        TEXT,
    -- queued | cloning | parsing | embedding | ready | failed
    status          TEXT NOT NULL DEFAULT 'queued',
    progress        JSONB NOT NULL DEFAULT '{}'::jsonb,
    stats           JSONB NOT NULL DEFAULT '{}'::jsonb,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    indexed_at      TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS files (
    id          BIGSERIAL PRIMARY KEY,
    repo_id     BIGINT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    language    TEXT,
    size_bytes  INTEGER NOT NULL,
    line_count  INTEGER NOT NULL,
    blob_sha    TEXT,
    content     TEXT NOT NULL,
    UNIQUE (repo_id, path)
);

CREATE TABLE IF NOT EXISTS chunks (
    id           BIGSERIAL PRIMARY KEY,
    repo_id      BIGINT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    -- code | doc   (phase 2: commit | pull_request | issue)
    source_type  TEXT NOT NULL,
    file_id      BIGINT REFERENCES files(id) ON DELETE CASCADE,
    path         TEXT,
    language     TEXT,
    -- function | method | class | module | section | window ...
    symbol_kind  TEXT,
    symbol_name  TEXT,
    start_line   INTEGER,
    end_line     INTEGER,
    content      TEXT NOT NULL,
    -- Identifier-expanded text for lexical search (camelCase / snake_case split).
    search_text  TEXT NOT NULL,
    metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding    vector({{EMBEDDING_DIM}}),
    tsv          tsvector GENERATED ALWAYS AS (to_tsvector('simple', search_text)) STORED
);

CREATE INDEX IF NOT EXISTS chunks_repo_idx ON chunks (repo_id);
CREATE INDEX IF NOT EXISTS chunks_file_idx ON chunks (file_id);
CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);
