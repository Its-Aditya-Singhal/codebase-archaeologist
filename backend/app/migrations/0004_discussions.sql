-- Discussion threads on pull requests and issues: conversation comments and
-- PR review comments (on a diff line). Upserted, like pull_requests/issues.
CREATE TABLE IF NOT EXISTS comments (
    repo_id        BIGINT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    comment_id     BIGINT NOT NULL,
    parent_type    TEXT NOT NULL,  -- pull_request | issue
    parent_number  INTEGER NOT NULL,
    kind           TEXT NOT NULL,  -- conversation | review
    author         TEXT,
    body           TEXT NOT NULL,
    path           TEXT,           -- review comments: the file commented on
    created_at     TIMESTAMPTZ,
    url            TEXT,
    PRIMARY KEY (repo_id, comment_id, kind)
);
CREATE INDEX IF NOT EXISTS comments_parent_idx ON comments (repo_id, parent_type, parent_number);
