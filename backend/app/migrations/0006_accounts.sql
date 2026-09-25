-- Accounts. Users sign up with email + password (scrypt hash); a login creates
-- a session whose token lives in an HttpOnly cookie and is stored here only as
-- its SHA-256 hash.
--
-- A repository is indexed once and shared by everyone who adds the same URL
-- (indexing is expensive), but each user only sees the repositories they
-- added (user_repositories). Investigations belong to one user.
CREATE TABLE IF NOT EXISTS users (
    id             BIGSERIAL PRIMARY KEY,
    email          TEXT NOT NULL,
    name           TEXT NOT NULL DEFAULT '',
    password_hash  TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at  TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS users_email_key ON users (lower(email));

CREATE TABLE IF NOT EXISTS sessions (
    token_hash   TEXT PRIMARY KEY,
    user_id      BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    user_agent   TEXT
);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions (user_id);

CREATE TABLE IF NOT EXISTS user_repositories (
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    repo_id    BIGINT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, repo_id)
);
CREATE INDEX IF NOT EXISTS user_repositories_repo_idx ON user_repositories (repo_id);

-- NULL for investigations made before accounts existed; the first account
-- to sign up adopts them (and those repositories).
ALTER TABLE investigations
    ADD COLUMN IF NOT EXISTS user_id BIGINT REFERENCES users(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS investigations_user_idx ON investigations (user_id, updated_at DESC);
