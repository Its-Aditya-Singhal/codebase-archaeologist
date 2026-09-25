"""Hybrid retrieval: dense vectors + lexical search fused with Reciprocal Rank Fusion.

Dense search catches paraphrase ("caching layer" ~ `RedisStore`); lexical search
catches exact identifiers the embedding model has never seen. Neither alone is
reliable on code, so both run and ranks are fused.

`focus` lets a caller pin the investigation to a file/line range (e.g. the
function the user selected). Chunks overlapping the focus are always included
first, and other chunks from the same file get a boost.

Provenance: for the code under investigation (the focus, or else the symbol the
question names) the commits that changed it, their pull requests and the issues
behind them are pinned right after it. See app/history/provenance.py.
"""

from dataclasses import asdict, dataclass, field

from app.db import connection
from app.embeddings import get_embedder
from app.history.provenance import (
    ProvenanceItem,
    file_provenance,
    linked_records,
    range_provenance,
)
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
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


_COLUMNS = """id, source_type, path, language, symbol_kind, symbol_name,
              start_line, end_line, content, metadata"""


def search(repo_id: int, question: str, limit: int = 12, focus: Focus | None = None,
           history: bool = True) -> list[RetrievedChunk]:
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
                    WHERE repo_id = %s AND source_type = 'code' AND symbol_name IS NOT NULL
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

        repo = conn.execute("SELECT local_path, head_sha FROM repositories WHERE id = %s",
                            (repo_id,)).fetchone()

    # 6. Provenance of the code under investigation.
    history_items: list[ProvenanceItem] = []
    if history and repo and repo["local_path"] and repo["head_sha"]:
        anchor = _provenance_anchor(focus, rows_by_id, matched, scores)
        if anchor and anchor[1] is not None:
            history_items = range_provenance(repo_id, repo["local_path"], repo["head_sha"],
                                             anchor[0], anchor[1], anchor[2])
        elif anchor:
            history_items = file_provenance(repo_id, repo["local_path"], anchor[0])
    history_ids = {h.id for h in history_items}

    results: list[RetrievedChunk] = [
        RetrievedChunk(**rows_by_id[cid], score=1.0, matched_by=matched[cid]) for cid in pinned
    ]
    for h in history_items:
        role = h.metadata.get("role") or h.metadata.get("relation") or "history"
        results.append(RetrievedChunk(**vars(h), score=1.0, matched_by=["history", role]))
    ranked = [
        cid for cid, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        if cid not in pinned and cid not in history_ids
    ]
    budget = limit + len(results)
    for cid in ranked:
        row = rows_by_id[cid]
        if _overlaps_selected(row, results):
            continue
        results.append(RetrievedChunk(**row, score=round(scores.get(cid, 1.0), 5),
                                      matched_by=matched[cid]))
        if len(results) >= budget:
            break
    return _expand_history_hits(repo_id, results)


def _expand_history_hits(repo_id: int, results: list[RetrievedChunk], max_added: int = 4
                         ) -> list[RetrievedChunk]:
    """One hop along links for history hits found by search: a matching commit
    brings its pull request, a matching PR brings the issues it fixes."""
    present = {(r.source_type, r.symbol_name) for r in results}
    wanted: list[tuple[int, str, str]] = []  # (result index, type, "#n")
    for i, r in enumerate(results):
        if "history" in r.matched_by:
            continue  # provenance already expanded these
        if r.source_type == "commit" and r.metadata.get("pr"):
            wanted.append((i, "pull_request", f"#{r.metadata['pr']}"))
        elif r.source_type == "pull_request":
            wanted.append((i, "issue", r.symbol_name or ""))
    wanted = [w for w in wanted if (w[1], w[2]) not in present]
    if not wanted:
        return results
    records = linked_records(repo_id, wanted)
    out: list[RetrievedChunk] = []
    added = 0
    for i, r in enumerate(results):
        out.append(r)
        for item in records.get(i, []):
            key = (item.source_type, item.symbol_name)
            if added >= max_added or key in present:
                continue
            present.add(key)
            added += 1
            out.append(RetrievedChunk(**vars(item), score=r.score,
                                      matched_by=["linked", item.metadata.get("relation", "")]))
    return out


def _provenance_anchor(focus: Focus | None, rows_by_id: dict, matched: dict, scores: dict
                       ) -> tuple[str, int | None, int | None] | None:
    """The code whose history to pull: the user's selection, else the
    best-scoring code chunk the question names by symbol."""
    if focus is not None:
        return focus.path, focus.start_line, focus.end_line or focus.start_line
    named = [cid for cid, labels in matched.items()
             if "symbol" in labels and rows_by_id[cid]["source_type"] == "code"]
    if not named:
        return None
    best = rows_by_id[max(named, key=lambda cid: scores.get(cid, 0.0))]
    return best["path"], best["start_line"], best["end_line"]


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
