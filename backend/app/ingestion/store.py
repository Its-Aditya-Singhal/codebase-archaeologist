"""Embed and persist retrieval chunks (shared by code and history ingestion)."""

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


def store_chunks(repo_id: int, rows: Sequence[ChunkRow],
                 on_progress: Callable[[int, int], None] | None = None) -> None:
    embedder = get_embedder()
    for start in range(0, len(rows), EMBED_BATCH):
        batch = rows[start : start + EMBED_BATCH]
        vectors = embedder.embed_documents([r.embed_text for r in batch])
        params = [
            (repo_id, r.source_type, r.file_id, r.path, r.language, r.symbol_kind,
             r.symbol_name, r.start_line, r.end_line, r.content,
             expand_identifiers(r.search_text), Jsonb(r.metadata), vec)
            for r, vec in zip(batch, vectors, strict=True)
        ]
        with connection() as conn, conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO chunks (repo_id, source_type, file_id, path, language,
                       symbol_kind, symbol_name, start_line, end_line, content,
                       search_text, metadata, embedding)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                params,
            )
        if on_progress:
            on_progress(start + len(batch), len(rows))


def delete_chunks(repo_id: int, source_types: Sequence[str]) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM chunks WHERE repo_id = %s AND source_type = ANY(%s)",
                     (repo_id, list(source_types)))
