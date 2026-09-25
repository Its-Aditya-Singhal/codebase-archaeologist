"""Questions asked of the knowledge graph.

- `locate`: the graph node for a file or a selected line range
- `neighborhood`: what a symbol/file is connected to (callers, callees, bases,
  imports, dependencies) and the history chain behind it (issue -> PR -> commit)
- `impact`: what could break if it changes: the reverse closure over calls,
  inheritance and imports, the tests that reach it, and files that historically
  change together with it
- `related_code`: one-hop code neighbours, used by retrieval
"""

import re
from collections import defaultdict
from dataclasses import dataclass

from app.db import connection
from app.history.git_log import range_history
from app.history.provenance import select_range_commits

NODE_COLUMNS = "id, kind, key, label, path, start_line, end_line, chunk_id, data"
TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|spec|specs|testing)(/|$)|(^|/)test_[^/]*$|_test\.\w+$"
    r"|\.(test|spec)\.[cm]?[jt]sx?$|Tests?\.\w+$")
_NODE_SEL = ", ".join("n." + c for c in NODE_COLUMNS.split(", "))
MAX_IMPACT_NODES = 400
COCHANGE_MAX_FILES = 30  # ignore sweeping commits (formatting, renames) for co-change


def is_test_path(path: str | None) -> bool:
    return bool(path and TEST_PATH.search(path))


def _node(row: dict) -> dict:
    return {k: row[k] for k in ("id", "kind", "key", "label", "path", "start_line", "end_line",
                                "data")}


# ---------------------------------------------------------------------- locate


def locate(repo_id: int, path: str, start: int | None = None, end: int | None = None
           ) -> dict | None:
    """Innermost symbol containing the range, else the symbol best covered by
    it, else the file."""
    with connection() as conn:
        if start is not None:
            end = end or start
            row = conn.execute(
                f"""SELECT {NODE_COLUMNS} FROM graph_nodes
                    WHERE repo_id = %s AND kind = 'symbol' AND path = %s
                      AND start_line <= %s AND end_line >= %s
                    ORDER BY end_line - start_line LIMIT 1""",
                (repo_id, path, start, end)).fetchone()
            if row is None:
                row = conn.execute(
                    f"""SELECT {NODE_COLUMNS} FROM graph_nodes
                        WHERE repo_id = %s AND kind = 'symbol' AND path = %s
                          AND start_line >= %s AND end_line <= %s
                        ORDER BY end_line - start_line DESC LIMIT 1""",
                    (repo_id, path, start, end)).fetchone()
            if row is not None:
                return row
        return conn.execute(
            f"SELECT {NODE_COLUMNS} FROM graph_nodes WHERE repo_id = %s AND kind = 'file' "
            "AND key = %s", (repo_id, path)).fetchone()


def _scope(conn, target: dict) -> set[int]:
    """The nodes that stand for the target: a class includes its methods, a file
    includes everything defined in it."""
    if target["kind"] == "file":
        rows = conn.execute("SELECT id FROM graph_nodes WHERE repo_id = %s AND path = %s",
                            (target["repo_id"], target["path"])).fetchall()
        return {r["id"] for r in rows} | {target["id"]}
    scope, frontier = {target["id"]}, [target["id"]]
    for _ in range(3):
        if not frontier:
            break
        rows = conn.execute(
            "SELECT dst FROM graph_edges WHERE src = ANY(%s) AND kind = 'contains'",
            (frontier,)).fetchall()
        frontier = [r["dst"] for r in rows if r["dst"] not in scope]
        scope.update(frontier)
    return scope


# ---------------------------------------------------------------- neighborhood


@dataclass
class _Rel:
    node: dict
    side: str  # in | out
    depth: int
    relation: str  # caller | callee | subclass | base | importer | imports | uses
    confidence: float
    weight: float
    via: str | None
    anchor: int  # the node this one connects to (target, or a depth-1 neighbour)


def neighborhood(repo: dict, path: str, start: int | None, end: int | None,
                 per_column: int = 12, history: bool = True) -> dict | None:
    target = locate(repo["id"], path, start, end)
    if target is None:
        return None
    target = {**target, "repo_id": repo["id"]}
    with connection() as conn:
        scope = _scope(conn, target)
        if target["kind"] == "file":
            rels, totals = _file_relations(conn, target, scope, per_column)
        else:
            rels, totals = _symbol_relations(conn, target, scope, per_column)
        container = conn.execute(
            f"""SELECT {_NODE_SEL} FROM graph_edges e JOIN graph_nodes n ON n.id = e.src
                WHERE e.dst = %s AND e.kind IN ('defines', 'contains') LIMIT 1""",
            (target["id"],)).fetchone()
        members = conn.execute(
            f"""SELECT {_NODE_SEL} FROM graph_edges e JOIN graph_nodes n ON n.id = e.dst
                WHERE e.src = %s AND e.kind IN ('defines', 'contains')
                ORDER BY n.start_line""", (target["id"],)).fetchall()
        lane = _history_lane(conn, repo, target) if history else {"nodes": [], "edges": []}

    nodes = [{**_node(target), "side": "target", "depth": 0}]
    seen = {target["id"]}
    for r in rels:
        if r.node["id"] not in seen:
            seen.add(r.node["id"])
            nodes.append({**_node(r.node), "side": r.side, "depth": r.depth,
                          "relation": r.relation})
    # Each relation is drawn as one edge between the neighbour and what it
    # connects to: the target at depth 1, a depth-1 neighbour at depth 2.
    edges = [{"src": r.node["id"] if r.side == "in" else r.anchor,
              "dst": r.anchor if r.side == "in" else r.node["id"],
              "kind": r.relation, "confidence": r.confidence, "weight": r.weight, "via": r.via}
             for r in rels]
    return {
        "target": {**_node(target), "members": len(members)},
        "container": _node(container) if container else None,
        "members": [_node(m) for m in members[:60]],
        "nodes": nodes + lane["nodes"],
        "edges": edges + lane["edges"],
        "totals": totals,
    }


def _neighbors(conn, ids: set[int], kinds: tuple[str, ...], direction: str, exclude: set[int],
               limit: int) -> tuple[list[dict], int]:
    """Nodes one hop from `ids` along `kinds` edges, aggregated per node, best
    first. direction 'in': edges pointing at ids; 'out': edges leaving ids."""
    if not ids:
        return [], 0
    near, far = ("dst", "src") if direction == "in" else ("src", "dst")
    rows = conn.execute(
        f"""SELECT {_NODE_SEL}, sum(e.weight) AS weight, max(e.confidence) AS confidence,
                   (array_agg(e.data->>'via' ORDER BY e.confidence DESC))[1] AS via,
                   (array_agg(e.kind ORDER BY e.confidence DESC))[1] AS edge_kind,
                   (array_agg(e.{near} ORDER BY e.confidence DESC))[1] AS anchor
            FROM graph_edges e JOIN graph_nodes n ON n.id = e.{far}
            WHERE e.{near} = ANY(%s) AND e.kind = ANY(%s) AND NOT (e.{far} = ANY(%s))
            GROUP BY n.id
            ORDER BY max(e.confidence) DESC, sum(e.weight) DESC, n.label""",
        (list(ids), list(kinds), list(exclude))).fetchall()
    # Production code before tests: tests dominate call counts but rarely
    # answer "what depends on this".
    rows.sort(key=lambda r: is_test_path(r["path"]))
    return rows[:limit], len(rows)


def _symbol_relations(conn, target: dict, scope: set[int], per_column: int):
    rels: list[_Rel] = []
    totals: dict[str, int] = {}

    callers, totals["callers"] = _neighbors(conn, scope, ("calls",), "in", scope, per_column)
    subclasses, totals["subclasses"] = _neighbors(conn, {target["id"]}, ("inherits",), "in",
                                                  scope, 6)
    callees, totals["callees"] = _neighbors(conn, scope, ("calls",), "out", scope, per_column)
    bases, totals["bases"] = _neighbors(conn, {target["id"]}, ("inherits",), "out", scope, 6)
    for rows, side, relation in ((callers, "in", "caller"), (subclasses, "in", "subclass"),
                                 (callees, "out", "callee"), (bases, "out", "base")):
        rels += [_rel(r, side, 1, relation, target["id"]) for r in rows]

    shown = scope | {r["id"] for r in callers + callees + subclasses + bases}
    callers2, totals["callers_depth2"] = _neighbors(
        conn, {r["id"] for r in callers}, ("calls",), "in", shown, per_column)
    shown |= {r["id"] for r in callers2}
    callees2, totals["callees_depth2"] = _neighbors(
        conn, {r["id"] for r in callees}, ("calls",), "out", shown, per_column)
    rels += [_rel(r, "in", 2, "caller", r["anchor"]) for r in callers2]
    rels += [_rel(r, "out", 2, "callee", r["anchor"]) for r in callees2]

    uses = _dependencies_used(conn, target)
    totals["dependencies"] = len(uses)
    rels += [_Rel(u, "out", 1, "uses", 1.0, 1.0, "import", target["id"]) for u in uses[:8]]
    return rels, totals


def _file_relations(conn, target: dict, scope: set[int], per_column: int):
    rels: list[_Rel] = []
    totals: dict[str, int] = {}
    fid = {target["id"]}
    importers, totals["importers"] = _neighbors(conn, fid, ("imports",), "in", scope, per_column)
    imports, _ = _neighbors(conn, fid, ("imports",), "out", scope, 200)
    files = [r for r in imports if r["kind"] == "file"]
    external = [r for r in imports if r["kind"] != "file"]
    totals["imports"], totals["dependencies"] = len(files), len(external)
    external.sort(key=lambda r: (bool(r["data"].get("stdlib")), r["kind"] != "dependency"))
    rels += [_rel(r, "in", 1, "importer", target["id"]) for r in importers]
    rels += [_rel(r, "out", 1, "imports", target["id"]) for r in files[:per_column]]
    rels += [_rel(r, "out", 1, "uses", target["id"]) for r in external[:8]]

    shown = scope | {r["id"] for r in importers + imports}
    importers2, totals["importers_depth2"] = _neighbors(
        conn, {r["id"] for r in importers}, ("imports",), "in", shown, per_column)
    rels += [_rel(r, "in", 2, "importer", r["anchor"]) for r in importers2]
    return rels, totals


def _rel(row: dict, side: str, depth: int, relation: str, anchor: int) -> _Rel:
    return _Rel(row, side, depth, relation, float(row["confidence"]), float(row["weight"]),
                row["via"], anchor)


def _dependencies_used(conn, target: dict) -> list[dict]:
    """External packages the file imports whose bound names appear in the symbol."""
    rows = conn.execute(
        f"""SELECT {_NODE_SEL}, e.data AS edge_data
            FROM graph_nodes f
            JOIN graph_edges e ON e.src = f.id AND e.kind = 'imports'
            JOIN graph_nodes n ON n.id = e.dst AND n.kind IN ('dependency', 'module')
            WHERE f.repo_id = %s AND f.kind = 'file' AND f.key = %s""",
        (target["repo_id"], target["path"])).fetchall()
    if not rows:
        return []
    text = conn.execute("SELECT content FROM files WHERE repo_id = %s AND path = %s",
                        (target["repo_id"], target["path"])).fetchone()
    lines = (text["content"] if text else "").splitlines()
    content = "\n".join(lines[target["start_line"] - 1: target["end_line"]])
    used = []
    for r in rows:
        names = (r["edge_data"] or {}).get("bound") or []
        if any(re.search(rf"(?<![\w.]){re.escape(n)}\b", content) for n in names):
            used.append(r)
    used.sort(key=lambda r: (bool(r["data"].get("stdlib")), r["kind"] != "dependency"))
    return used


def _history_lane(conn, repo: dict, target: dict) -> dict:
    """issue -> pull request -> commit -> target, for the commit that introduced
    the code and its most recent changes."""
    picks: list[tuple[str, str]] = []  # (sha, role)
    if target["kind"] == "symbol" and repo.get("local_path") and repo.get("head_sha"):
        chosen = select_range_commits(range_history(
            repo["local_path"], repo["head_sha"], target["path"], target["start_line"],
            target["end_line"]))
        picks = [(c.sha, role) for c, role in chosen]
    else:
        rows = conn.execute(
            """SELECT n.key FROM graph_edges e JOIN graph_nodes n ON n.id = e.src
               WHERE e.dst = (SELECT id FROM graph_nodes WHERE repo_id = %s AND kind = 'file'
                              AND key = %s) AND e.kind = 'modifies'
               ORDER BY n.data->>'date' DESC""", (repo["id"], target["path"])).fetchall()
        picks = [(r["key"], "modified") for r in rows[:3]]
        if len(rows) > 3:
            picks.append((rows[-1]["key"], "introduced"))
        elif rows:
            picks[-1] = (rows[-1]["key"], "introduced")
    if not picks:
        return {"nodes": [], "edges": []}
    roles = dict(picks)
    commits = conn.execute(
        f"SELECT {NODE_COLUMNS} FROM graph_nodes WHERE repo_id = %s AND kind = 'commit' "
        "AND key = ANY(%s)", (repo["id"], list(roles))).fetchall()
    ids = [c["id"] for c in commits]
    to_prs = conn.execute(
        f"""SELECT e.src AS from_id, e.kind AS edge_kind, {_NODE_SEL}
            FROM graph_edges e JOIN graph_nodes n ON n.id = e.dst
            WHERE e.src = ANY(%s) AND e.kind IN ('merged_in', 'part_of')""",
        (ids,)).fetchall()
    pr_ids = list({r["id"] for r in to_prs})
    to_issues = conn.execute(
        f"""SELECT e.src AS from_id, e.kind AS edge_kind, {_NODE_SEL}
            FROM graph_edges e JOIN graph_nodes n ON n.id = e.dst
            WHERE e.src = ANY(%s) AND e.kind IN ('fixes', 'mentions') AND n.kind = 'issue'
            ORDER BY (e.kind = 'fixes') DESC""", (pr_ids + ids,)).fetchall()

    nodes, edges, seen = [], [], set()

    def add(row: dict, depth: int, relation: str, **extra) -> None:
        if row["id"] not in seen:
            seen.add(row["id"])
            nodes.append({**_node(row), "side": "history", "depth": depth,
                          "relation": relation, **extra})

    for c in sorted(commits, key=lambda c: c["data"].get("date", ""), reverse=True):
        add(c, 1, "commit", role=roles.get(c["key"]))
        edges.append({"src": c["id"], "dst": target["id"], "kind": "modifies",
                      "confidence": 1.0, "weight": 1.0, "via": roles.get(c["key"])})
    for r in to_prs:
        add(r, 2, "pull_request")
        edges.append({"src": r["from_id"], "dst": r["id"], "kind": r["edge_kind"],
                      "confidence": 1.0, "weight": 1.0, "via": None})
    for r in to_issues[:6]:
        add(r, 3, "issue")
        edges.append({"src": r["from_id"], "dst": r["id"], "kind": r["edge_kind"],
                      "confidence": 1.0, "weight": 1.0, "via": None})
    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------- impact


def impact(repo: dict, path: str, start: int | None, end: int | None, max_depth: int = 3
           ) -> dict | None:
    target = locate(repo["id"], path, start, end)
    if target is None:
        return None
    target = {**target, "repo_id": repo["id"]}
    with connection() as conn:
        scope = _scope(conn, target)
        kinds = ["calls", "inherits"] + (["imports"] if target["kind"] == "file" else [])
        # Reverse breadth-first closure. Confidence of a path is the product of
        # its edges' confidences; keep the best path to each node.
        reached: dict[int, dict] = {}
        frontier = {i: 1.0 for i in scope}
        for depth in range(1, max_depth + 1):
            if not frontier or len(reached) >= MAX_IMPACT_NODES:
                break
            rows = conn.execute(
                """SELECT src, dst, kind, confidence FROM graph_edges
                   WHERE dst = ANY(%s) AND kind = ANY(%s)""",
                (list(frontier), kinds)).fetchall()
            nxt: dict[int, float] = {}
            for r in rows:
                if r["src"] in scope:
                    continue
                conf = frontier[r["dst"]] * r["confidence"]
                known = reached.get(r["src"])
                if known is None:
                    reached[r["src"]] = {"depth": depth, "confidence": conf, "through": r["dst"],
                                         "edge": r["kind"]}
                    nxt[r["src"]] = conf
                elif known["depth"] == depth and conf > known["confidence"]:
                    known.update(confidence=conf, through=r["dst"], edge=r["kind"])
                    nxt[r["src"]] = conf
            frontier = nxt
        nodes = {r["id"]: r for r in conn.execute(
            f"SELECT {NODE_COLUMNS} FROM graph_nodes WHERE id = ANY(%s)",
            (list(reached) + list(scope),)).fetchall()}
        cochange, file_commits = _co_changed(conn, repo["id"], target["path"])
        churn = conn.execute(
            """SELECT count(*) AS commits, count(DISTINCT c.author_email) AS authors,
                      max(c.authored_at) AS last_changed
               FROM commit_files cf JOIN commits c ON c.id = cf.commit_id
               WHERE cf.repo_id = %s AND cf.path = %s""",
            (repo["id"], target["path"])).fetchone()

    dependents = []
    for nid, info in reached.items():
        n = nodes.get(nid)
        if n is None or n["kind"] not in ("symbol", "file"):
            continue
        through = nodes.get(info["through"])
        dependents.append({
            **_node(n), "depth": info["depth"], "confidence": round(info["confidence"], 2),
            "edge": info["edge"], "through": through["label"] if through else None,
            "is_test": is_test_path(n["path"]),
        })
    dependents.sort(key=lambda d: (d["depth"], -d["confidence"], d["path"] or "", d["label"]))

    files: dict[str, dict] = {}
    for d in dependents:
        f = files.setdefault(d["path"], {"path": d["path"], "symbols": 0, "depth": d["depth"],
                                         "is_test": d["is_test"]})
        f["symbols"] += 1
        f["depth"] = min(f["depth"], d["depth"])
    code = [d for d in dependents if not d["is_test"]]
    tests = [d for d in dependents if d["is_test"]]
    summary = {
        "direct": sum(1 for d in code if d["depth"] == 1),
        "transitive": len(code),
        "files": len({d["path"] for d in code} - {target["path"]}),
        "tests": len(tests),
        "test_files": len({d["path"] for d in tests}),
    }
    return {
        "target": _node(target),
        "summary": summary,
        "risk": _risk(summary, cochange, churn),
        "dependents": dependents[:150],
        "files": sorted(files.values(), key=lambda f: (f["is_test"], f["depth"], -f["symbols"]))
        [:60],
        "co_changed": cochange,
        "churn": {**churn, "file_commits": file_commits},
    }


def _co_changed(conn, repo_id: int, path: str, limit: int = 8) -> tuple[list[dict], int]:
    rows = conn.execute(
        """WITH mine AS (
               SELECT cf.commit_id FROM commit_files cf JOIN commits c ON c.id = cf.commit_id
               WHERE cf.repo_id = %(r)s AND cf.path = %(p)s AND c.files_changed <= %(max)s)
           SELECT cf.path, count(DISTINCT cf.commit_id) AS together,
                  (SELECT count(*) FROM mine) AS total
           FROM commit_files cf JOIN mine USING (commit_id)
           WHERE cf.path <> %(p)s
             AND EXISTS (SELECT 1 FROM files f WHERE f.repo_id = %(r)s AND f.path = cf.path)
           GROUP BY cf.path HAVING count(DISTINCT cf.commit_id) >= 2
           ORDER BY together DESC, cf.path LIMIT %(lim)s""",
        {"r": repo_id, "p": path, "max": COCHANGE_MAX_FILES, "lim": limit}).fetchall()
    total = rows[0]["total"] if rows else 0
    return [{"path": r["path"], "together": r["together"],
             "ratio": round(r["together"] / r["total"], 2) if r["total"] else 0,
             "is_test": is_test_path(r["path"])} for r in rows], total


def _risk(summary: dict, cochange: list[dict], churn: dict) -> dict:
    """A transparent heuristic: every point comes with the reason it was given."""
    score, reasons = 0, []
    direct, files = summary["direct"], summary["files"]
    if direct >= 10:
        score += 3
        reasons.append(f"{direct} direct callers or importers")
    elif direct >= 3:
        score += 2
        reasons.append(f"{direct} direct callers or importers")
    elif direct >= 1:
        score += 1
        reasons.append(f"{direct} direct caller{'s' if direct > 1 else ''}")
    if files >= 8:
        score += 2
        reasons.append(f"reaches code in {files} other files")
    elif files >= 3:
        score += 1
        reasons.append(f"reaches code in {files} other files")
    if summary["tests"] == 0 and summary["transitive"] > 0:
        score += 2
        reasons.append("no test code reaches it in the static call graph")
    strong = [c for c in cochange if c["ratio"] >= 0.4 and c["together"] >= 3]
    if strong:
        score += 1
        reasons.append(f"usually changes together with {strong[0]['path']} "
                       f"({int(strong[0]['ratio'] * 100)}% of its commits)")
    if (churn.get("commits") or 0) >= 50:
        score += 1
        reasons.append(f"its file changed in {churn['commits']} commits")
    level = "high" if score >= 5 else "medium" if score >= 2 else "low"
    return {"level": level, "score": score, "reasons": reasons}


# ------------------------------------------------------------------- retrieval


def related_code(repo_id: int, path: str, start: int, end: int, limit: int = 6
                 ) -> tuple[dict | None, list[dict]]:
    """Code one hop from the symbol at path:start-end, best first: its callers,
    callees, base classes and subclasses, each with the chunk that holds it.
    Production code is preferred over tests, which tend to dominate call counts."""
    target = locate(repo_id, path, start, end)
    if target is None or target["kind"] != "symbol":
        return target, []
    target = {**target, "repo_id": repo_id}
    out: list[dict] = []
    with connection() as conn:
        scope = _scope(conn, target)
        for kinds, direction, relation, n in ((("calls",), "in", "caller", limit),
                                              (("calls",), "out", "callee", limit),
                                              (("inherits",), "out", "base", 3),
                                              (("inherits",), "in", "subclass", 3)):
            rows, _ = _neighbors(conn, scope if relation in ("caller", "callee")
                                 else {target["id"]}, kinds, direction, scope, 50)
            rows = [r for r in rows if r["kind"] == "symbol" and r["chunk_id"] is not None]
            out += [{**r, "relation": relation} for r in rows[:n]]
    # Interleave so callers and callees both make it under a tight budget.
    by_rel: dict[str, list[dict]] = defaultdict(list)
    for r in out:
        by_rel[r["relation"]].append(r)
    mixed: list[dict] = []
    while any(by_rel.values()) and len(mixed) < limit:
        for rel in ("caller", "callee", "base", "subclass"):
            if by_rel[rel]:
                mixed.append(by_rel[rel].pop(0))
    return target, mixed


def describe_impact(repo: dict, path: str, start: int | None, end: int | None) -> str | None:
    """Impact analysis as plain text, for use as answer evidence."""
    result = impact(repo, path, start, end)
    if result is None or result["target"]["kind"] not in ("symbol", "file"):
        return None
    s, t = result["summary"], result["target"]
    lines = [f"Static dependency analysis for {t['label']} ({t['path']}). "
             "Edges come from name-based resolution of calls, imports and inheritance; "
             "dynamic dispatch, reflection and string-based lookups are not visible to it.",
             f"Direct dependents (non-test): {s['direct']}; transitive (up to 3 hops): "
             f"{s['transitive']} in {s['files']} other files; test functions reaching it: "
             f"{s['tests']} in {s['test_files']} test files.",
             f"Heuristic change risk: {result['risk']['level']} "
             f"({'; '.join(result['risk']['reasons']) or 'no dependents found'})."]
    direct = [d for d in result["dependents"] if d["depth"] == 1][:15]
    if direct:
        lines.append("Direct dependents:")
        lines += [f"- {d['label']} ({d['path']}:{d['start_line']}) {d['edge']} it"
                  f"{'' if d['confidence'] >= 0.9 else f', confidence {d['confidence']}'}"
                  for d in direct]
    further = [d for d in result["dependents"] if d["depth"] > 1 and not d["is_test"]][:10]
    if further:
        lines.append("Further (indirect) dependents:")
        lines += [f"- {d['label']} ({d['path']}) via {d['through']}, {d['depth']} hops"
                  for d in further]
    if result["co_changed"]:
        lines.append(f"Files that changed in the same commits as {t['path']} "
                     f"(of {result['churn']['file_commits']} commits):")
        lines += [f"- {c['path']}: {c['together']} commits ({int(c['ratio'] * 100)}%)"
                  for c in result["co_changed"][:6]]
    return "\n".join(lines)
