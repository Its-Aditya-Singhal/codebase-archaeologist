"""Hybrid retrieval: dense vectors + lexical search fused with Reciprocal Rank Fusion.

Dense search catches paraphrase ("caching layer" ~ `RedisStore`); lexical search
catches exact identifiers the embedding model has never seen. Neither alone is
reliable on code, so both run and ranks are fused.

`focus` lets a caller pin the investigation to a file/line range (e.g. the
function the user selected). Chunks overlapping the focus are always included
first, and other chunks from the same file get a boost.

Graph: the code under investigation (the focus, or else the symbol the question
names) pulls in its callers, callees and base classes from the knowledge graph,
and questions about change impact get the graph's impact analysis as evidence.

Provenance: for the same code, the commits that changed it, their pull requests
and the issues behind them are pinned right after it. See app/history/provenance.py.
"""

import re
from dataclasses import asdict, dataclass, field

from app.db import connection
from app.embeddings import get_embedder
from app.graph.query import describe_impact, related_code
from app.history.provenance import (
    ProvenanceItem,
    file_provenance,
    linked_records,
    range_provenance,
)
from app.text import query_identifiers, query_terms

RRF_K = 60
CANDIDATES = 50
IMPACT_QUESTION = re.compile(
    r"\b(break|breaks|breaking|impact|affect\w*|depend\w*|dependents|callers?|who (uses|calls)"
    r"|where\b.*\bused|usages?|refactor\w*|safe to|risk\w*|blast radius"
    r"|(if|when) (i|we|you|someone) (change|modify|remove|delete|rename)\w*)\b", re.IGNORECASE)
RELATION_TEXT = {"caller": "calls {}", "callee": "called by {}", "base": "base class of {}",
                 "subclass": "subclass of {}"}


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

        if focus is not None:
            for cid, row in rows_by_id.items():
                if row["path"] == focus.path and cid not in pinned:
                    scores[cid] = scores.get(cid, 0.0) * 1.5
                    matched[cid].append("same-file")

        repo = conn.execute("SELECT local_path, head_sha FROM repositories WHERE id = %s",
                            (repo_id,)).fetchone()

    # 5. Graph neighbours of the code under investigation: what calls it, what it
    # calls, its base classes. Falls back to a lexical search for mentions of the
    # symbol when the graph has nothing (unsupported language, no graph yet).
    anchor = _code_anchor(focus_rows, rows_by_id, matched, scores)
    graph_target = None
    if anchor is not None:
        graph_target, related = related_code(repo_id, anchor["path"], anchor["start_line"],
                                             anchor["end_line"])
        if related:
            _add_graph_neighbours(related, graph_target, pinned, add, rows_by_id, matched)
        elif focus_symbols:
            _add_references(repo_id, focus_symbols[0], pinned, add)

    # 6. Impact analysis for "what would break / who uses this" questions.
    impact_chunk = None
    if graph_target is not None and IMPACT_QUESTION.search(question):
        text = describe_impact({"id": repo_id}, graph_target["path"],
                               graph_target["start_line"] if graph_target["kind"] == "symbol"
                               else None, graph_target["end_line"])
        if text:
            impact_chunk = RetrievedChunk(
                id=-graph_target["id"], source_type="graph", path=graph_target["path"],
                language=None, symbol_kind="impact", symbol_name=graph_target["label"],
                start_line=graph_target["start_line"], end_line=graph_target["end_line"],
                content=text, score=1.0, matched_by=["graph", "impact"],
                metadata={"node_kind": graph_target["kind"]})

    # 7. Provenance of the code under investigation.
    history_items: list[ProvenanceItem] = []
    if history and repo and repo["local_path"] and repo["head_sha"]:
        where = _provenance_anchor(focus, anchor)
        if where and where[1] is not None:
            history_items = range_provenance(repo_id, repo["local_path"], repo["head_sha"],
                                             where[0], where[1], where[2])
        elif where:
            history_items = file_provenance(repo_id, repo["local_path"], where[0])
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
    if impact_chunk is not None:
        results.insert(len(pinned), impact_chunk)
    return _expand_history_hits(repo_id, results)


def _code_anchor(focus_rows: list[dict], rows_by_id: dict, matched: dict, scores: dict
                 ) -> dict | None:
    """The code the question is about: the selection, else the best-scoring code
    chunk the question names by symbol."""
    if focus_rows:
        return focus_rows[0]
    named = [cid for cid, labels in matched.items()
             if "symbol" in labels and rows_by_id[cid]["source_type"] == "code"]
    if not named:
        return None
    return rows_by_id[max(named, key=lambda cid: scores.get(cid, 0.0))]


def _add_graph_neighbours(related: list[dict], target: dict, pinned: list[int], add,
                          rows_by_id: dict, matched: dict) -> None:
    ids = [r["chunk_id"] for r in related if r["chunk_id"] not in pinned]
    if not ids:
        return
    with connection() as conn:
        rows = {r["id"]: r for r in conn.execute(
            f"SELECT {_COLUMNS} FROM chunks WHERE id = ANY(%s)", (ids,)).fetchall()}
    # Several methods of one small class share its chunk; the first relation wins.
    firsts = {}
    for r in related:
        if r["chunk_id"] in rows:
            firsts.setdefault(r["chunk_id"], r)
    add([rows[cid] for cid in firsts], "graph", weight=1.5)
    for cid, r in firsts.items():
        text = RELATION_TEXT[r["relation"]].format(target["label"])
        if r["confidence"] < 0.9:
            text += f" (inferred by name, confidence {r['confidence']:.1f})"
        row = rows_by_id[cid]
        row["metadata"] = {**(row["metadata"] or {}), "graph": text}
        matched[cid].append(r["relation"])


def _add_references(repo_id: int, symbol: str, pinned: list[int], add) -> None:
    """Chunks that mention the symbol by name (call sites, docs, tests)."""
    short = symbol.replace("::", ".").rsplit(".", 1)[-1]
    if len(short) < 3 or not query_terms(short):
        return
    with connection() as conn:
        add(conn.execute(
            f"""SELECT {_COLUMNS} FROM chunks, phraseto_tsquery('simple', %s) q
                WHERE repo_id = %s AND tsv @@ q AND NOT (id = ANY(%s))
                ORDER BY ts_rank_cd(tsv, q) DESC LIMIT 15""",
            (short, repo_id, pinned),
        ).fetchall(), "reference", weight=1.5)


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


def _provenance_anchor(focus: Focus | None, anchor: dict | None
                       ) -> tuple[str, int | None, int | None] | None:
    """The code whose history to pull: the user's selection (a range or a whole
    file), else the code the question names."""
    if focus is not None:
        return focus.path, focus.start_line, focus.end_line or focus.start_line
    if anchor is None:
        return None
    return anchor["path"], anchor["start_line"], anchor["end_line"]


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
