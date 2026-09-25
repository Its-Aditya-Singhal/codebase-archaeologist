"""Embed and persist retrieval chunks (shared by code and history ingestion).

Re-indexing is incremental: `sync_chunks` matches new chunks to stored ones by
the hash of their embedding input, keeps those vectors (updating line numbers,
metadata and the like in place) and only embeds what is new or changed.
"""

import hashlib
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from psycopg.types.json import Jsonb

from app.db import connection
from app.embeddings import get_embedder
from app.text import expand_identifiers

EMBED_BATCH = 128


@dataclass
class ChunkRow:
    source_type: str
    content: str
    embed_text: str  # what the embedding model sees (context header + content)
    search_text: str  # raw text for lexical search; identifiers get expanded on insert
    file_id: int | None = None
    path: str | None = None
    language: str | None = None
    symbol_kind: str | None = None
    symbol_name: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def embed_hash(self) -> str:
        return hashlib.sha1(self.embed_text.encode("utf-8")).hexdigest()


@dataclass
class SyncStats:
    reused: int = 0
    embedded: int = 0
    deleted: int = 0


def _fields(repo_id: int, r: ChunkRow) -> tuple:
    return (repo_id, r.source_type, r.file_id, r.path, r.language, r.symbol_kind,
            r.symbol_name, r.start_line, r.end_line, r.content,
            expand_identifiers(r.search_text), Jsonb(r.metadata), r.embed_hash)


def sync_chunks(repo_id: int, source_types: Sequence[str], rows: Sequence[ChunkRow],
                on_progress: Callable[[int, int], None] | None = None) -> SyncStats:
    """Make the stored chunks of `source_types` equal to `rows`, re-embedding
    only chunks whose embedding input is new."""
    with connection() as conn:
        existing = conn.execute(
            "SELECT id, embed_hash FROM chunks WHERE repo_id = %s AND source_type = ANY(%s)",
            (repo_id, list(source_types))).fetchall()
    pool: dict[str, list[int]] = defaultdict(list)
    for r in existing:
        if r["embed_hash"]:
            pool[r["embed_hash"]].append(r["id"])
    reuse: list[tuple[int, ChunkRow]] = []
    fresh: list[ChunkRow] = []
    for row in rows:
        ids = pool.get(row.embed_hash)
        if ids:
            reuse.append((ids.pop(), row))
        else:
            fresh.append(row)
    kept = {cid for cid, _ in reuse}
    stale = [r["id"] for r in existing if r["id"] not in kept]

    with connection() as conn, conn.transaction(), conn.cursor() as cur:
        if stale:
            cur.execute("DELETE FROM chunks WHERE id = ANY(%s)", (stale,))
        if reuse:
            cur.executemany(
                """UPDATE chunks SET repo_id = %s, source_type = %s, file_id = %s, path = %s,
                       language = %s, symbol_kind = %s, symbol_name = %s, start_line = %s,
                       end_line = %s, content = %s, search_text = %s, metadata = %s,
                       embed_hash = %s
                   WHERE id = %s""",
                [(*_fields(repo_id, row), cid) for cid, row in reuse],
            )
    store_chunks(repo_id, fresh, on_progress)
    return SyncStats(reused=len(reuse), embedded=len(fresh), deleted=len(stale))


def store_chunks(repo_id: int, rows: Sequence[ChunkRow],
                 on_progress: Callable[[int, int], None] | None = None) -> None:
    if not rows:
        if on_progress:
            on_progress(0, 0)
        return
    embedder = get_embedder()
    for start in range(0, len(rows), EMBED_BATCH):
        batch = rows[start : start + EMBED_BATCH]
        vectors = embedder.embed_documents([r.embed_text for r in batch])
        params = [(*_fields(repo_id, r), vec) for r, vec in zip(batch, vectors, strict=True)]
        with connection() as conn, conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO chunks (repo_id, source_type, file_id, path, language,
                       symbol_kind, symbol_name, start_line, end_line, content,
                       search_text, metadata, embed_hash, embedding)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                params,
            )
        if on_progress:
            on_progress(start + len(batch), len(rows))
