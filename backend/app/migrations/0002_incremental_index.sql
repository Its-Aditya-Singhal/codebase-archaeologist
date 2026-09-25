-- Incremental re-indexing: a chunk whose embedding input is unchanged keeps its
-- vector. `embed_hash` is sha1 of the text the embedding model saw.
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embed_hash TEXT;
CREATE INDEX IF NOT EXISTS chunks_embed_hash_idx ON chunks (repo_id, embed_hash);
