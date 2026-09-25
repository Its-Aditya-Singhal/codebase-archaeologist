"""Repository ingestion: clone -> walk -> chunk -> embed -> store."""

import logging
import traceback
from collections import Counter
from dataclasses import dataclass

from psycopg.types.json import Jsonb

from app.config import get_settings
from app.db import connection
from app.embeddings import get_embedder
from app.ingestion.chunker import Chunk, chunk_file, detect_language
from app.ingestion.filters import decode_text, should_index_path
from app.ingestion.repo_source import head_info, parse_repo_ref, sync_checkout, tracked_files
from app.text import expand_identifiers

log = logging.getLogger(__name__)

EMBED_BATCH = 128


@dataclass
class ParsedFile:
    path: str
    language: str | None
    blob_sha: str
    content: str
    chunks: list[Chunk]


def _set_status(repo_id: int, status: str, **fields) -> None:
    sets = ["status = %(status)s"]
    params = {"id": repo_id, "status": status}
    for key, value in fields.items():
        sets.append(f"{key} = %({key})s")
        params[key] = Jsonb(value) if isinstance(value, dict) else value
    with connection() as conn:
        conn.execute(f"UPDATE repositories SET {', '.join(sets)} WHERE id = %(id)s", params)


def embedding_text(path: str, language: str | None, chunk: Chunk) -> str:
    """What the embedding model sees: location context first (so it survives
    truncation), then the chunk body."""
    header = [f"File: {path}"]
    if language:
        header.append(f"Language: {language}")
    if chunk.symbol_name:
        header.append(f"{chunk.symbol_kind.capitalize()}: {chunk.symbol_name}")
    return "\n".join(header) + "\n\n" + chunk.content


def ingest_repository(repo_id: int) -> None:
    settings = get_settings()
    try:
        with connection() as conn:
            row = conn.execute("SELECT url FROM repositories WHERE id = %s", (repo_id,)).fetchone()
        if row is None:
            return
        ref = parse_repo_ref(row["url"])

        _set_status(repo_id, "cloning", error=None, progress={"step": "Fetching repository"})
        sync_checkout(ref)
        head_sha, branch = head_info(ref.local_path)

        _set_status(repo_id, "parsing", progress={"step": "Parsing files"})
        candidates = [(p, sha) for p, sha in tracked_files(ref.local_path) if should_index_path(p)]
        candidates = candidates[: settings.max_files]
        parsed: list[ParsedFile] = []
        skipped = 0
        for i, (path, blob_sha) in enumerate(candidates):
            full = ref.local_path / path
            try:
                if not full.is_file() or full.stat().st_size > settings.max_file_bytes:
                    skipped += 1
                    continue
                text = decode_text(full.read_bytes())
            except OSError:
                text = None
            if text is None or not text.strip():
                skipped += 1
                continue
            language = detect_language(path)
            chunks = chunk_file(path, text, language)
            parsed.append(ParsedFile(path, language, blob_sha, text, chunks))
            if i % 200 == 0:
                _set_status(repo_id, "parsing", progress={
                    "step": "Parsing files", "done": i, "total": len(candidates)})

        total_chunks = sum(len(f.chunks) for f in parsed)
        _set_status(repo_id, "embedding", progress={
            "step": "Embedding chunks", "done": 0, "total": total_chunks})

        with connection() as conn:
            with conn.transaction():
                conn.execute("DELETE FROM files WHERE repo_id = %s", (repo_id,))
                conn.execute("DELETE FROM chunks WHERE repo_id = %s", (repo_id,))
                file_ids: dict[str, int] = {}
                for f in parsed:
                    file_ids[f.path] = conn.execute(
                        """INSERT INTO files (repo_id, path, language, size_bytes, line_count,
                                              blob_sha, content)
                           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (repo_id, f.path, f.language, len(f.content.encode()),
                         f.content.count("\n") + 1, f.blob_sha, f.content),
                    ).fetchone()["id"]

        embedder = get_embedder()
        pending = [(f, c) for f in parsed for c in f.chunks]
        for start in range(0, len(pending), EMBED_BATCH):
            batch = pending[start : start + EMBED_BATCH]
            vectors = embedder.embed_documents(
                [embedding_text(f.path, f.language, c) for f, c in batch])
            rows = [
                (repo_id, c.source_type, file_ids[f.path], f.path, f.language, c.symbol_kind,
                 c.symbol_name, c.start_line, c.end_line, c.content,
                 expand_identifiers(f"{f.path}\n{c.symbol_name or ''}\n{c.content}"),
                 Jsonb(c.metadata), vec)
                for (f, c), vec in zip(batch, vectors, strict=True)
            ]
            with connection() as conn:
                with conn.cursor() as cur:
                    cur.executemany(
                        """INSERT INTO chunks (repo_id, source_type, file_id, path, language,
                               symbol_kind, symbol_name, start_line, end_line, content,
                               search_text, metadata, embedding)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        rows,
                    )
            _set_status(repo_id, "embedding", progress={
                "step": "Embedding chunks", "done": start + len(batch), "total": total_chunks})

        languages = Counter(f.language or "other" for f in parsed)
        stats = {
            "files": len(parsed),
            "skipped_files": skipped,
            "chunks": total_chunks,
            "symbols": sum(1 for f in parsed for c in f.chunks if c.symbol_name),
            "languages": dict(languages.most_common(12)),
        }
        with connection() as conn:
            conn.execute(
                """UPDATE repositories SET status = 'ready', progress = '{}'::jsonb, stats = %s,
                       head_sha = %s, default_branch = %s, local_path = %s, error = NULL,
                       indexed_at = now()
                   WHERE id = %s""",
                (Jsonb(stats), head_sha, branch, str(ref.local_path), repo_id),
            )
        log.info("Indexed repo %s: %s", repo_id, stats)
    except Exception as exc:
        log.error("Ingestion failed for repo %s\n%s", repo_id, traceback.format_exc())
        _set_status(repo_id, "failed", error=str(exc)[:2000], progress={})
