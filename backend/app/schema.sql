-- Codebase Archaeologist schema.
--
-- `chunks` is deliberately source-agnostic: code, docs, commits, pull requests
-- and issues all flow through the same retrieval path. Relationships between
-- records live in `links` (and, in phase 3, a fuller entity/edge graph).

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
    -- code | doc | commit | pull_request | issue
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

-- ---------------------------------------------------------------- phase 2: history

-- Resumable GitHub sync state per listing, e.g.
-- {"pull_requests": {"watermark": "...", "next_page": 4, "pending_watermark": "..."}}
ALTER TABLE repositories ADD COLUMN IF NOT EXISTS sync JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS commits (
    id              BIGSERIAL PRIMARY KEY,
    repo_id         BIGINT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    sha             TEXT NOT NULL,
    parent_shas     TEXT[] NOT NULL DEFAULT '{}',
    author_name     TEXT,
    author_email    TEXT,
    authored_at     TIMESTAMPTZ,
    committed_at    TIMESTAMPTZ,
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL DEFAULT '',
    files_changed   INTEGER NOT NULL DEFAULT 0,
    insertions      INTEGER NOT NULL DEFAULT 0,
    deletions       INTEGER NOT NULL DEFAULT 0,
    UNIQUE (repo_id, sha)
);
CREATE INDEX IF NOT EXISTS commits_repo_date_idx ON commits (repo_id, authored_at DESC);

CREATE TABLE IF NOT EXISTS commit_files (
    commit_id   BIGINT NOT NULL REFERENCES commits(id) ON DELETE CASCADE,
    repo_id     BIGINT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    insertions  INTEGER,
    deletions   INTEGER
);
CREATE INDEX IF NOT EXISTS commit_files_path_idx ON commit_files (repo_id, path);
CREATE INDEX IF NOT EXISTS commit_files_commit_idx ON commit_files (commit_id);

-- Pull requests and issues fetched from the GitHub API. Upserted, never bulk
-- deleted on re-index, so data accumulates across rate-limited fetches.
CREATE TABLE IF NOT EXISTS pull_requests (
    id                BIGSERIAL PRIMARY KEY,
    repo_id           BIGINT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    number            INTEGER NOT NULL,
    title             TEXT NOT NULL,
    body              TEXT NOT NULL DEFAULT '',
    state             TEXT NOT NULL,
    author            TEXT,
    labels            TEXT[] NOT NULL DEFAULT '{}',
    created_at        TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ,
    closed_at         TIMESTAMPTZ,
    merged_at         TIMESTAMPTZ,
    merge_commit_sha  TEXT,
    url               TEXT,
    UNIQUE (repo_id, number)
);

CREATE TABLE IF NOT EXISTS issues (
    id          BIGSERIAL PRIMARY KEY,
    repo_id     BIGINT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    number      INTEGER NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL DEFAULT '',
    state       TEXT NOT NULL,
    author      TEXT,
    labels      TEXT[] NOT NULL DEFAULT '{}',
    comments    INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ,
    updated_at  TIMESTAMPTZ,
    closed_at   TIMESTAMPTZ,
    url         TEXT,
    UNIQUE (repo_id, number)
);

-- Typed relationships between history records, e.g.
--   commit:<sha> -[merged_in | part_of]-> pull_request:<n>
--   pull_request:<n> -[fixes | mentions]-> issue:<n>
-- The seed of the phase 3 knowledge graph.
CREATE TABLE IF NOT EXISTS links (
    repo_id   BIGINT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    src_type  TEXT NOT NULL,
    src_key   TEXT NOT NULL,
    dst_type  TEXT NOT NULL,
    dst_key   TEXT NOT NULL,
    kind      TEXT NOT NULL,
    PRIMARY KEY (repo_id, src_type, src_key, dst_type, dst_key, kind)
);
CREATE INDEX IF NOT EXISTS links_dst_idx ON links (repo_id, dst_type, dst_key);
