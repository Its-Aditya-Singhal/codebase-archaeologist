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


def init_db() -> None:
    """Create the extension/tables, then open the pool."""
    global _pool
    settings = get_settings()
    schema = (Path(__file__).parent / "schema.sql").read_text()
    schema = schema.replace("{{EMBEDDING_DIM}}", str(settings.embedding_dim))
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute(schema)
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
