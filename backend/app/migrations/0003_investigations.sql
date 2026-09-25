-- Saved investigations ("case files"): a thread of questions about one
-- repository, each turn with its focus, evidence and answer, so follow-up
-- questions have context and an investigation can be reopened later.
CREATE TABLE IF NOT EXISTS investigations (
    id          BIGSERIAL PRIMARY KEY,
    repo_id     BIGINT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS investigations_repo_idx ON investigations (repo_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS investigation_turns (
    id                BIGSERIAL PRIMARY KEY,
    investigation_id  BIGINT NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    position          INTEGER NOT NULL,
    question          TEXT NOT NULL,
    focus             JSONB,
    mode              TEXT NOT NULL DEFAULT 'answer',  -- answer | agent
    steps             JSONB NOT NULL DEFAULT '[]'::jsonb,  -- agent tool calls
    evidence          JSONB NOT NULL DEFAULT '[]'::jsonb,
    answer            TEXT NOT NULL DEFAULT '',
    answered_by       JSONB,
    -- done | error | stopped
    status            TEXT NOT NULL,
    error             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (investigation_id, position)
);
