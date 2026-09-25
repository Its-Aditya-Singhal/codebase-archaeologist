from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import get_settings

_pool: ConnectionPool | None = None


def _configure(conn: psycopg.Connection) -> None:
    register_vector(conn)
    # Repo-filtered HNSW searches otherwise return too few rows once the
    # filter discards candidates; iterative scans keep walking the graph.
    conn.execute("SET hnsw.iterative_scan = relaxed_order")
    conn.commit()


MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_LOCK = 7_300_451  # pg advisory lock key: one migrator at a time


def migrate(database_url: str) -> list[str]:
    """Apply pending migrations (app/migrations/NNNN_name.sql) in order, each in
    its own transaction, recording them in `schema_migrations`. Returns the
    versions applied. Migrations are written to be idempotent, so a database
    created before this table existed is adopted safely."""
    dim = str(get_settings().embedding_dim)
    applied: list[str] = []
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK,))
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                                version TEXT PRIMARY KEY,
                                applied_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
            done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                version = path.stem
                if version in done:
                    continue
                sql = path.read_text().replace("{{EMBEDDING_DIM}}", dim)
                with conn.transaction():
                    conn.execute(sql)
                    conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)",
                                 (version,))
                applied.append(version)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK,))
    return applied


def init_db() -> None:
    """Apply pending migrations, then open the pool."""
    global _pool
    settings = get_settings()
    migrate(settings.database_url)
    _pool = ConnectionPool(
        settings.database_url,
        min_size=1,
        max_size=10,
        configure=_configure,
        kwargs={"row_factory": dict_row},
        open=True,
    )


def close_db() -> None:
    if _pool is not None:
        _pool.close()


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    if _pool is None:
        raise RuntimeError("Database not initialised; call init_db() first")
    with _pool.connection() as conn:
        yield conn
