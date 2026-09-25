"""Hybrid retrieval: dense vectors + lexical search fused with Reciprocal Rank Fusion.

Dense search catches paraphrase ("caching layer" ~ `RedisStore`); lexical search
catches exact identifiers the embedding model has never seen. Neither alone is
reliable on code, so both run and ranks are fused.

`focus` lets a caller pin the investigation to a file/line range (e.g. the
function the user selected). Chunks overlapping the focus are always included
first, and other chunks from the same file get a boost.
"""

from dataclasses import asdict, dataclass

from app.db import connection
from app.embeddings import get_embedder
from app.text import query_identifiers, query_terms

RRF_K = 60
CANDIDATES = 50


@dataclass
class Focus:
    path: str
    start_line: int | None = None
    end_line: int | None = None


@dataclass
class RetrievedChunk:
    id: int
    source_type: str
    path: str | None
    language: str | None
    symbol_kind: str | None
    symbol_name: str | None
    start_line: int | None
    end_line: int | None
    content: str
    score: float
    matched_by: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


_COLUMNS = """id, source_type, path, language, symbol_kind, symbol_name,
              start_line, end_line, content"""


def search(repo_id: int, question: str, limit: int = 12, focus: Focus | None = None
           ) -> list[RetrievedChunk]:
    scores: dict[int, float] = {}
    matched: dict[int, list[str]] = {}
    rows_by_id: dict[int, dict] = {}

    def add(rows, label: str, weight: float = 1.0):
        for rank, row in enumerate(rows, start=1):
            cid = row["id"]
            rows_by_id[cid] = row
            scores[cid] = scores.get(cid, 0.0) + weight / (RRF_K + rank)
            matched.setdefault(cid, []).append(label)

    with connection() as conn:
        # 1. The focused code (if any) is pinned first, unconditionally.
        pinned: list[int] = []
        focus_rows: list[dict] = []
        if focus is not None and focus.start_line is not None:
            focus_rows = conn.execute(
                f"""SELECT {_COLUMNS} FROM chunks
                    WHERE repo_id = %s AND path = %s
                      AND start_line <= %s AND end_line >= %s
                    ORDER BY (end_line - start_line) LIMIT 3""",
                (repo_id, focus.path, focus.end_line or focus.start_line, focus.start_line),
            ).fetchall()
            for row in focus_rows:
                rows_by_id[row["id"]] = row
                matched.setdefault(row["id"], []).append("focus")
                pinned.append(row["id"])

        # Questions about a selection are often vague ("why does this exist?"), so
        # the selected code itself becomes part of the retrieval query.
        focus_symbols = [r["symbol_name"] for r in focus_rows if r["symbol_name"]]
        retrieval_text = question
        if focus_rows:
            primary = focus_rows[0]
            retrieval_text = (
                f"{question}\n{primary['symbol_name'] or ''}\n{primary['content'][:1500]}")
        qvec = get_embedder().embed_query(retrieval_text)
        terms = query_terms(" ".join([question, *focus_symbols]))
        tsquery = " | ".join(terms)

        # 2. Dense retrieval.
        add(conn.execute(
            f"""SELECT {_COLUMNS} FROM chunks WHERE repo_id = %s
                ORDER BY embedding <=> %s LIMIT %s""",
            (repo_id, qvec, CANDIDATES),
        ).fetchall(), "semantic")

        # 3. Lexical retrieval.
        if tsquery:
            add(conn.execute(
                f"""SELECT {_COLUMNS} FROM chunks, to_tsquery('simple', %s) q
                    WHERE repo_id = %s AND tsv @@ q
                    ORDER BY ts_rank_cd(tsv, q) DESC LIMIT %s""",
                (tsquery, repo_id, CANDIDATES),
            ).fetchall(), "keyword")

        # 4. Symbol-name hits are strong evidence ("why does fooBar ..." -> fooBar).
        # Match the unqualified name so `enqueue_call` finds `Queue.enqueue_call`.
        idents = query_identifiers(question)
        if idents:
            add(conn.execute(
                f"""SELECT {_COLUMNS} FROM chunks
                    WHERE repo_id = %s AND symbol_name IS NOT NULL
                      AND (lower(symbol_name) = ANY(%s)
                           OR lower(regexp_replace(symbol_name, '^.*[.:]', '')) = ANY(%s))
                    ORDER BY (lower(symbol_name) = ANY(%s)) DESC, (end_line - start_line) DESC
                    LIMIT 10""",
                (repo_id, idents, idents, idents),
            ).fetchall(), "symbol", weight=2.0)

        # 5. References: other chunks that mention the focused symbol (call sites,
        # docs, tests). A lexical stand-in until the graph layer has real edges.
        for sym in focus_symbols[:1]:
            short = sym.replace("::", ".").rsplit(".", 1)[-1]
            ref_terms = query_terms(short)
            if len(short) < 3 or not ref_terms:
                continue
            add(conn.execute(
                f"""SELECT {_COLUMNS} FROM chunks, phraseto_tsquery('simple', %s) q
                    WHERE repo_id = %s AND tsv @@ q AND NOT (id = ANY(%s))
                    ORDER BY ts_rank_cd(tsv, q) DESC LIMIT 15""",
                (short, repo_id, pinned),
            ).fetchall(), "reference", weight=1.5)

        if focus is not None:
            for cid, row in rows_by_id.items():
                if row["path"] == focus.path and cid not in pinned:
                    scores[cid] = scores.get(cid, 0.0) * 1.5
                    matched[cid].append("same-file")

    ranked = pinned + [
        cid for cid, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        if cid not in pinned
    ]
    results: list[RetrievedChunk] = []
    for cid in ranked:
        row = rows_by_id[cid]
        if _overlaps_selected(row, results):
            continue
        results.append(RetrievedChunk(**row, score=round(scores.get(cid, 1.0), 5),
                                      matched_by=matched[cid]))
        if len(results) >= limit:
            break
    return results


def _overlaps_selected(row: dict, selected: list[RetrievedChunk]) -> bool:
    """Drop near-duplicates: window chunks overlap by design, and a class header
    can overlap its methods. Keep the higher-ranked one."""
    for s in selected:
        if s.path != row["path"] or s.start_line is None or row["start_line"] is None:
            continue
        overlap = min(s.end_line, row["end_line"]) - max(s.start_line, row["start_line"]) + 1
        shorter = min(s.end_line - s.start_line, row["end_line"] - row["start_line"]) + 1
        if overlap > 0 and overlap / shorter > 0.6:
            return True
    return False
