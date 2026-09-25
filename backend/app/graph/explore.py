"""Whole-graph exploration: the API behind a pan/zoom relationship explorer.

- `overview`: the repository's architecture at file or directory level, with
  dependency edges aggregated from imports, calls and inheritance, and each
  node's size (symbols), churn (commits) and whether it is test code
- `search`: find nodes by name
- `expand`: any node's neighbours along chosen edge kinds, optionally limited
  to a time window (commits, pull requests, issues by date)
- `node_detail`: one node with its degree per edge kind and direction
"""

import posixpath
from collections import defaultdict

from app.db import connection
from app.graph.query import NODE_COLUMNS, _node, is_test_path

CODE_EDGES = ("imports", "calls", "inherits")
DATED_KINDS = ("commit", "pull_request", "issue")


def _group(path: str, level: str, depth: int) -> str:
    if level == "file":
        return path
    parts = posixpath.dirname(path).split("/") if "/" in path else ["."]
    return "/".join(parts[:depth]) or "."


def overview(repo_id: int, level: str = "file", depth: int = 2, limit: int = 150,
             include_tests: bool = True, include_packages: bool = True) -> dict:
    with connection() as conn:
        edges = conn.execute(
            """SELECT s.path AS a, d.path AS b, d.kind AS dst_kind, d.id AS dst_id, e.kind,
                      sum(e.weight) AS w
               FROM graph_edges e
               JOIN graph_nodes s ON s.id = e.src
               JOIN graph_nodes d ON d.id = e.dst
               WHERE e.repo_id = %s AND e.kind = ANY(%s) AND s.path IS NOT NULL
               GROUP BY 1, 2, 3, 4, 5""", (repo_id, list(CODE_EDGES))).fetchall()
        files = conn.execute(
            """SELECT f.path, f.line_count,
                      (SELECT count(*) FROM graph_nodes n WHERE n.repo_id = f.repo_id
                         AND n.kind = 'symbol' AND n.path = f.path) AS symbols,
                      (SELECT count(*) FROM graph_edges m JOIN graph_nodes fn ON fn.id = m.dst
                         WHERE fn.repo_id = f.repo_id AND fn.kind = 'file' AND fn.key = f.path
                           AND m.kind = 'modifies') AS commits
               FROM files f WHERE f.repo_id = %s""", (repo_id,)).fetchall()
        packages = {r["id"]: r for r in conn.execute(
            f"""SELECT {NODE_COLUMNS} FROM graph_nodes
                WHERE repo_id = %s AND kind IN ('dependency', 'module')""",
            (repo_id,)).fetchall()}

    nodes: dict[str, dict] = {}
    for f in files:
        if not include_tests and is_test_path(f["path"]):
            continue
        key = _group(f["path"], level, depth)
        n = nodes.setdefault(key, {"id": f"{level}:{key}", "kind": level, "label": key,
                                   "path": key, "files": 0, "lines": 0, "symbols": 0,
                                   "commits": 0, "tests": 0, "in": 0, "out": 0})
        n["files"] += 1
        n["lines"] += f["line_count"]
        n["symbols"] += f["symbols"]
        n["commits"] += f["commits"]
        n["tests"] += is_test_path(f["path"])

    links: dict[tuple[str, str], dict] = defaultdict(lambda: {"weight": 0.0, "kinds": {}})
    package_use: dict[int, set[str]] = defaultdict(set)
    for e in edges:
        a = _group(e["a"], level, depth)
        if a not in nodes:
            continue
        if e["dst_kind"] in ("dependency", "module"):
            package_use[e["dst_id"]].add(a)
            continue
        if e["b"] is None:
            continue
        b = _group(e["b"], level, depth)
        if b not in nodes or a == b:
            continue
        link = links[(a, b)]
        link["weight"] += float(e["w"])
        link["kinds"][e["kind"]] = link["kinds"].get(e["kind"], 0) + float(e["w"])
    for a, b in links:
        nodes[a]["out"] += 1
        nodes[b]["in"] += 1

    # Keep the most connected / most changed nodes when the repository is large.
    ranked = sorted(nodes.values(), key=lambda n: -(n["in"] + n["out"] + n["commits"] / 10))
    kept = {n["path"] for n in ranked[:limit]}
    out_nodes = [n for n in ranked if n["path"] in kept]
    out_edges = [{"src": f"{level}:{a}", "dst": f"{level}:{b}", "weight": round(v["weight"], 1),
                  "kinds": v["kinds"]}
                 for (a, b), v in links.items() if a in kept and b in kept]
    if include_packages:
        used = sorted(((pid, users) for pid, users in package_use.items()
                       if pid in packages and not packages[pid]["data"].get("stdlib")),
                      key=lambda kv: -len(kv[1]))
        for pid, users in used[:20]:
            p = packages[pid]
            pid_s = f"{p['kind']}:{p['label']}"
            out_nodes.append({"id": pid_s, "kind": p["kind"], "label": p["label"],
                              "path": None, "users": len(users),
                              "ecosystem": p["data"].get("ecosystem")})
            out_edges += [{"src": f"{level}:{u}", "dst": pid_s, "weight": 1.0,
                           "kinds": {"imports": 1}} for u in users if u in kept]
    return {"level": level, "nodes": out_nodes, "edges": out_edges,
            "total_nodes": len(nodes), "truncated": len(nodes) > len(kept)}


def search(repo_id: int, q: str, kinds: list[str] | None = None, limit: int = 20) -> list[dict]:
    q = q.strip()
    if not q:
        return []
    with connection() as conn:
        rows = conn.execute(
            f"""SELECT {NODE_COLUMNS},
                       CASE WHEN lower(label) = lower(%(q)s) THEN 0
                            WHEN lower(regexp_replace(label, '^.*[.:/]', '')) = lower(%(q)s)
                              THEN 1
                            WHEN label ILIKE %(prefix)s THEN 2 ELSE 3 END AS rank
                FROM graph_nodes
                WHERE repo_id = %(r)s AND label ILIKE %(contains)s
                  AND (%(kinds)s::text[] IS NULL OR kind = ANY(%(kinds)s::text[]))
                ORDER BY rank, length(label), label LIMIT %(limit)s""",
            {"r": repo_id, "q": q, "prefix": f"{q}%", "contains": f"%{q}%",
             "kinds": kinds or None, "limit": limit}).fetchall()
    return [_node(r) for r in rows]


def node_detail(repo_id: int, node_id: int) -> dict | None:
    with connection() as conn:
        node = conn.execute(
            f"SELECT {NODE_COLUMNS} FROM graph_nodes WHERE id = %s AND repo_id = %s",
            (node_id, repo_id)).fetchone()
        if node is None:
            return None
        degree = conn.execute(
            """SELECT kind, count(*) FILTER (WHERE src = %(n)s) AS out,
                      count(*) FILTER (WHERE dst = %(n)s) AS "in"
               FROM graph_edges WHERE src = %(n)s OR dst = %(n)s GROUP BY kind""",
            {"n": node_id}).fetchall()
    return {**_node(node), "degree": {d["kind"]: {"in": d["in"], "out": d["out"]}
                                      for d in degree}}


def expand(repo_id: int, node_id: int, kinds: list[str] | None = None,
           direction: str = "both", since: str | None = None, until: str | None = None,
           limit: int = 40) -> dict | None:
    """Neighbours of a node. Dated neighbours (commits, PRs, issues) outside
    [since, until] are left out; others are always included."""
    if node_detail(repo_id, node_id) is None:
        return None
    dirs = {"out": ["out"], "in": ["in"], "both": ["out", "in"]}[direction]
    out_nodes: dict[int, dict] = {}
    out_edges: list[dict] = []
    totals: dict[str, int] = {}
    with connection() as conn:
        for d in dirs:
            near, far = ("src", "dst") if d == "out" else ("dst", "src")
            rows = conn.execute(
                f"""SELECT {', '.join('n.' + c for c in NODE_COLUMNS.split(', '))},
                           e.kind AS edge_kind, e.weight, e.confidence, e.data->>'via' AS via
                    FROM graph_edges e JOIN graph_nodes n ON n.id = e.{far}
                    WHERE e.{near} = %(id)s
                      AND (%(kinds)s::text[] IS NULL OR e.kind = ANY(%(kinds)s::text[]))
                      AND (n.kind <> ALL(%(dated)s)
                           OR ((%(since)s::text IS NULL OR n.data->>'date' >= %(since)s)
                               AND (%(until)s::text IS NULL OR n.data->>'date' <= %(until)s)))
                    ORDER BY e.confidence DESC, e.weight DESC, n.data->>'date' DESC NULLS LAST""",
                {"id": node_id, "kinds": kinds or None, "dated": list(DATED_KINDS),
                 "since": since, "until": until}).fetchall()
            for r in rows:
                key = f"{d}:{r['edge_kind']}"
                totals[key] = totals.get(key, 0) + 1
            per_kind: dict[str, int] = defaultdict(int)
            for r in rows:
                if per_kind[r["edge_kind"]] >= limit:
                    continue
                per_kind[r["edge_kind"]] += 1
                out_nodes.setdefault(r["id"], _node(r))
                src, dst = (node_id, r["id"]) if d == "out" else (r["id"], node_id)
                out_edges.append({"src": src, "dst": dst, "kind": r["edge_kind"],
                                  "weight": r["weight"], "confidence": r["confidence"],
                                  "via": r["via"]})
    return {"node_id": node_id, "nodes": list(out_nodes.values()), "edges": out_edges,
            "totals": totals}
