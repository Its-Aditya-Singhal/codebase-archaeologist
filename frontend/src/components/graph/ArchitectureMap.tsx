"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  Code2,
  Crosshair,
  EyeOff,
  FolderTree,
  Loader2,
  Network,
  Search,
  Waypoints,
  X,
} from "lucide-react";
import {
  api,
  type Expansion,
  type Focus,
  type GraphEdge,
  type GraphNode,
  type GraphNodeDetail,
  type Overview,
  type OverviewNode,
} from "@/lib/api";
import { layout, type LayoutNode, type Positions } from "./forceLayout";
import { MapCanvas, type CanvasEdge, type CanvasNode } from "./MapCanvas";
import { KIND_GLYPH, NodeGlyph, nodeSubtitle } from "./parts";

const LAMP = "233,162,59";
const TEAL = "95,184,164";
const VIOLET = "169,150,224";
const GREY = "141,145,155";

const CODE_KINDS = ["calls", "imports", "inherits", "contains", "defines", "declares"];
const HISTORY_KINDS = ["modifies", "authored", "merged_in", "part_of", "fixes", "mentions"];
const DEFAULT_KINDS = ["calls", "imports", "inherits", "contains", "declares", "modifies", "merged_in", "fixes"];
const KIND_LABEL: Record<string, string> = {
  calls: "calls",
  imports: "imports",
  inherits: "inherits",
  contains: "contains",
  defines: "defines",
  declares: "declares",
  modifies: "commits",
  authored: "authors",
  merged_in: "PRs",
  part_of: "PR commits",
  fixes: "fixes",
  mentions: "mentions",
};
const EDGE_COLOR: Record<string, string> = {
  calls: `rgba(${LAMP},0.8)`,
  imports: `rgba(${TEAL},0.8)`,
  inherits: "rgba(233,228,216,0.8)",
  contains: `rgba(${GREY},0.5)`,
  defines: `rgba(${GREY},0.5)`,
  declares: `rgba(${VIOLET},0.8)`,
};

type Mode = { kind: "overview" } | { kind: "explore" };

interface ExploreGraph {
  root: number;
  nodes: Map<number, GraphNode>;
  edges: GraphEdge[];
  expanded: Set<number>;
  /** Neighbours left out by the per-kind limit, per expanded node. */
  hidden: Map<number, number>;
}

interface Filters {
  kinds: string[];
  direction: "in" | "out" | "both";
  since: string;
  until: string;
}

/** The repository as a map: its architecture (directories or files and the
 *  dependencies between them), and an explorer that grows the knowledge graph
 *  outward from any node, one click at a time. */
export function ArchitectureMap({
  repoId,
  onOpenCode,
  onInvestigate,
}: {
  repoId: number;
  onOpenCode: (path: string, start?: number | null, end?: number | null) => void;
  onInvestigate: (focus: Focus) => void;
}) {
  const [mode, setMode] = useState<Mode>({ kind: "overview" });
  const [error, setError] = useState<string | null>(null);

  // Overview
  const [level, setLevel] = useState<"dir" | "file">("dir");
  const [scope, setScope] = useState<string | null>(null);
  const [overview, setOverview] = useState<{ level: string; data: Overview } | null>(null);
  // Nodes the user dragged, for the layout they were dragged in.
  const [ovDragged, setOvDragged] = useState<{ key: string; pos: Positions } | null>(null);
  const [ovSelected, setOvSelected] = useState<string | null>(null);

  // Explorer
  const [graph, setGraph] = useState<ExploreGraph | null>(null);
  const [exPos, setExPos] = useState<Positions>(new Map());
  const [exSelected, setExSelected] = useState<number | null>(null);
  const [detail, setDetail] = useState<GraphNodeDetail | null>(null);
  const [filters, setFilters] = useState<Filters>({ kinds: DEFAULT_KINDS, direction: "both", since: "", until: "" });
  const [busy, setBusy] = useState(false);
  const [exFitKey, setExFitKey] = useState("start");

  useEffect(() => {
    let cancelled = false;
    api
      .overview(repoId, level, level === "dir" ? 2 : 2)
      .then((data) => {
        if (cancelled) return;
        setOverview({ level, data });
        setOvSelected(null);
      })
      .catch((e) => !cancelled && setError((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, [repoId, level]);

  // ---- overview -----------------------------------------------------------
  const ov = overview?.level === level ? overview.data : null;
  const ovVisible = useMemo(() => {
    if (!ov) return null;
    const pkg = (id: string) => id.startsWith("dependency:") || id.startsWith("module:");
    // In a directory: its own files, plus the packages they use.
    const inScope = (n: OverviewNode) =>
      !scope || (!!n.path && (n.path === scope || n.path.startsWith(scope + "/")));
    const core = new Set(ov.nodes.filter(inScope).map((n) => n.id));
    let edges = ov.edges.filter((e) => core.has(e.src) && (core.has(e.dst) || pkg(e.dst)));
    // At file level, keep each file's strongest dependencies so the map stays readable.
    if (ov.level === "file") {
      const bySrc = new Map<string, typeof edges>();
      for (const e of edges) bySrc.set(e.src, [...(bySrc.get(e.src) ?? []), e]);
      edges = [...bySrc.values()].flatMap((es) => {
        const code = es.filter((e) => !pkg(e.dst)).sort((a, b) => b.weight - a.weight);
        return [...code.slice(0, 4), ...es.filter((e) => pkg(e.dst))];
      });
    }
    const keep = new Set(core);
    for (const e of edges) keep.add(e.dst);
    return { ...ov, nodes: ov.nodes.filter((n) => keep.has(n.id)), edges };
  }, [ov, scope]);

  const maxCommits = useMemo(() => Math.max(1, ...(ov?.nodes.map((n) => n.commits ?? 0) ?? [1])), [ov]);

  const ovCanvas = useMemo(() => {
    if (!ovVisible) return null;
    const maxW = Math.max(1, ...ovVisible.edges.map((e) => e.weight));
    const nodes: CanvasNode[] = ovVisible.nodes.map((n) => {
      const pkg = n.kind === "dependency" || n.kind === "module";
      const churn = Math.sqrt((n.commits ?? 0) / maxCommits);
      const tone = pkg ? VIOLET : n.tests && n.tests === n.files ? TEAL : LAMP;
      const r = pkg
        ? 5 + Math.sqrt(n.users ?? 1) * 2.5
        : Math.max(7, Math.min(level === "dir" ? 42 : 26, 4 + Math.sqrt(n.lines ?? 0) / (level === "dir" ? 2.4 : 3)));
      return {
        id: n.id,
        label: n.label === "." ? "(repo root)" : n.label,
        r,
        fill: pkg ? `rgba(${VIOLET},0.16)` : `rgba(${tone},${0.1 + churn * 0.45})`,
        stroke: `rgba(${tone},${pkg ? 0.7 : 0.45 + churn * 0.55})`,
        glyph: pkg ? undefined : level === "dir" ? String(n.files ?? "") : undefined,
        prominent: pkg ? (n.users ?? 0) > 2 : level === "dir" || r > 16,
        title: pkg
          ? `${n.label} · ${n.kind === "dependency" ? "declared dependency" : "external module"} · used by ${n.users} ${level === "dir" ? "directories" : "files"}`
          : `${n.path}\n${n.files ? `${n.files} files · ` : ""}${n.lines} lines · ${n.symbols} symbols · ${n.commits} commits`,
      };
    });
    const edges: CanvasEdge[] = ovVisible.edges.map((e) => {
      const main = Object.entries(e.kinds).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "calls";
      const pkg = e.dst.startsWith("dependency:") || e.dst.startsWith("module:");
      return {
        id: `${e.src}>${e.dst}`,
        src: e.src,
        dst: e.dst,
        width: 0.8 + (Math.log1p(e.weight) / Math.log1p(maxW)) * 4,
        color: pkg ? `rgba(${VIOLET},0.55)` : (EDGE_COLOR[main] ?? `rgba(${LAMP},0.8)`),
        dashed: pkg,
        title: `${e.src.split(":")[1]} → ${e.dst.split(":")[1]}\n${Object.entries(e.kinds)
          .map(([k, v]) => `${k}: ${v}`)
          .join(" · ")}`,
      };
    });
    return { nodes, edges };
  }, [ovVisible, maxCommits, level]);

  const ovKey = `ov:${level}:${scope}:${ovVisible?.nodes.length ?? 0}`;
  const ovLayout = useMemo(
    () =>
      ovVisible && ovCanvas
        ? layout(
            ovCanvas.nodes.map((n) => ({ id: n.id, r: n.r })),
            ovVisible.edges.map((e) => ({ src: e.src, dst: e.dst, strength: 0.6 })),
          )
        : new Map() as Positions,
    [ovVisible, ovCanvas],
  );
  const ovPos = ovDragged?.key === ovKey ? ovDragged.pos : ovLayout;

  const ovNode = ovSelected ? ov?.nodes.find((n) => n.id === ovSelected) : undefined;

  // ---- explorer -----------------------------------------------------------
  const fetchExpansion = useCallback(
    (id: number, f: Filters) =>
      api.expand(repoId, id, {
        kinds: f.kinds,
        direction: f.direction,
        since: f.since ? `${f.since}-01-01` : undefined,
        until: f.until ? `${f.until}-12-31T23:59:59` : undefined,
      }),
    [repoId],
  );

  function merge(g: ExploreGraph, from: number, x: Expansion): ExploreGraph {
    const nodes = new Map(g.nodes);
    for (const n of x.nodes) if (!nodes.has(n.id)) nodes.set(n.id, n);
    const seen = new Set(g.edges.map((e) => `${e.src}>${e.dst}>${e.kind}`));
    const edges = [...g.edges];
    for (const e of x.edges) {
      const k = `${e.src}>${e.dst}>${e.kind}`;
      if (!seen.has(k)) {
        seen.add(k);
        edges.push(e);
      }
    }
    const total = Object.values(x.totals).reduce((a, b) => a + b, 0);
    const hidden = new Map(g.hidden).set(from, Math.max(0, total - x.nodes.length));
    return { ...g, nodes, edges, expanded: new Set(g.expanded).add(from), hidden };
  }

  function relayout(g: ExploreGraph, previous: Positions | undefined, near?: number) {
    const lnodes: LayoutNode[] = [...g.nodes.values()].map((n) => ({
      id: String(n.id),
      r: radiusOf(n, g.root),
      near: near !== undefined ? String(near) : undefined,
      pinned: n.id === g.root && !!previous?.size,
    }));
    return layout(
      lnodes,
      g.edges.map((e) => ({ src: String(e.src), dst: String(e.dst), strength: 0.7 })),
      { iterations: previous?.size ? 160 : 300, previous },
    );
  }

  async function explore(nodeId: number) {
    setBusy(true);
    setError(null);
    try {
      const [root, x] = await Promise.all([api.graphNode(repoId, nodeId), fetchExpansion(nodeId, filters)]);
      const g = merge(
        { root: nodeId, nodes: new Map([[nodeId, root]]), edges: [], expanded: new Set(), hidden: new Map() },
        nodeId,
        x,
      );
      setGraph(g);
      setExPos(relayout(g, undefined));
      setExSelected(nodeId);
      setDetail(root);
      setMode({ kind: "explore" });
      setExFitKey(`ex:${nodeId}:${Date.now()}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function expand(nodeId: number) {
    if (!graph || graph.expanded.has(nodeId)) return;
    setBusy(true);
    try {
      const x = await fetchExpansion(nodeId, filters);
      const g = merge(graph, nodeId, x);
      setGraph(g);
      setExPos(relayout(g, exPos, nodeId));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function hide(nodeId: number) {
    if (!graph || nodeId === graph.root) return;
    const nodes = new Map(graph.nodes);
    nodes.delete(nodeId);
    const edges = graph.edges.filter((e) => e.src !== nodeId && e.dst !== nodeId);
    const expanded = new Set(graph.expanded);
    expanded.delete(nodeId);
    setGraph({ ...graph, nodes, edges, expanded });
    setExSelected(null);
    setDetail(null);
  }

  // Re-run every expansion when the filters change, keeping positions.
  const filtersKey = JSON.stringify(filters);
  const lastFilters = useRef(filtersKey);
  useEffect(() => {
    if (!graph || lastFilters.current === filtersKey) return;
    lastFilters.current = filtersKey;
    let cancelled = false;
    setBusy(true);
    const ids = [...graph.expanded];
    Promise.all(ids.map((id) => fetchExpansion(id, filters)))
      .then((xs) => {
        if (cancelled) return;
        let g: ExploreGraph = {
          root: graph.root,
          nodes: new Map([[graph.root, graph.nodes.get(graph.root)!]]),
          edges: [],
          expanded: new Set(),
          hidden: new Map(),
        };
        xs.forEach((x, i) => {
          // Only keep expansions still reachable from what is on the map.
          if (g.nodes.has(ids[i])) g = merge(g, ids[i], x);
        });
        setGraph(g);
        setExPos((prev) => relayout(g, prev));
      })
      .catch((e) => !cancelled && setError((e as Error).message))
      .finally(() => !cancelled && setBusy(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtersKey]);

  useEffect(() => {
    if (exSelected === null || mode.kind !== "explore") return;
    if (detail?.id === exSelected) return;
    let cancelled = false;
    api
      .graphNode(repoId, exSelected)
      .then((d) => !cancelled && setDetail(d))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [exSelected, repoId, mode.kind, detail?.id]);

  const exCanvas = useMemo(() => {
    if (!graph) return null;
    const nodes: CanvasNode[] = [...graph.nodes.values()].map((n) => {
      const tone = toneOf(n);
      const isRoot = n.id === graph.root;
      const more = graph.expanded.has(n.id) ? "" : " · double-click to expand";
      return {
        id: String(n.id),
        label: shortLabel(n),
        r: radiusOf(n, graph.root),
        fill: `rgba(${tone},${isRoot ? 0.45 : graph.expanded.has(n.id) ? 0.3 : 0.12})`,
        stroke: `rgba(${tone},0.9)`,
        glyph: n.kind === "symbol" ? (KIND_GLYPH[String(n.data.kind)] ?? "·") : undefined,
        prominent: isRoot || graph.expanded.has(n.id) || n.kind !== "commit",
        title: `${n.label}\n${nodeSubtitle(n)}${more}`,
      };
    });
    const edges: CanvasEdge[] = graph.edges.map((e, i) => ({
      id: `${e.src}>${e.dst}>${e.kind}>${i}`,
      src: String(e.src),
      dst: String(e.dst),
      width: 1 + Math.min(2.5, Math.log1p(e.weight)),
      color: EDGE_COLOR[e.kind] ?? `rgba(${VIOLET},0.6)`,
      dashed: e.confidence < 0.8 || HISTORY_KINDS.includes(e.kind),
      title: `${KIND_LABEL[e.kind] ?? e.kind}${e.via ? ` · via ${e.via}` : ""} · confidence ${Math.round(e.confidence * 100)}%`,
    }));
    return { nodes, edges };
  }, [graph]);

  const exNode = exSelected !== null ? graph?.nodes.get(exSelected) : undefined;

  // ---- actions shared by both views --------------------------------------
  async function exploreOverviewNode(n: OverviewNode) {
    setBusy(true);
    try {
      const q = n.kind === "file" ? (n.path ?? n.label) : n.label;
      const hits = await api.searchGraph(repoId, q);
      const hit =
        n.kind === "file"
          ? hits.find((h) => h.kind === "file" && h.path === n.path)
          : hits.find((h) => (h.kind === "dependency" || h.kind === "module") && h.label === n.label);
      if (!hit) {
        setError(`“${n.label}” is not in the knowledge graph.`);
        setBusy(false);
        return;
      }
      await explore(hit.id);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  const canvas = mode.kind === "overview" ? ovCanvas : exCanvas;
  const positions = mode.kind === "overview" ? ovPos : exPos;

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-ink-700 bg-ink-950/95 px-3 py-2">
        {mode.kind === "overview" ? (
          <>
            <Segmented
              value={level}
              onChange={(v) => {
                setLevel(v);
                setScope(null);
              }}
              options={[
                { value: "dir", label: "Directories", icon: FolderTree },
                { value: "file", label: "Files", icon: Code2 },
              ]}
            />
            {scope ? (
              <span className="flex items-center gap-1 rounded-full border border-lamp/40 bg-lamp-soft px-2 py-0.5 font-mono text-[10.5px] text-lamp">
                in {scope}/
                <button onClick={() => setScope(null)} title="Show everything" className="hover:text-parchment">
                  <X className="size-3" />
                </button>
              </span>
            ) : null}
            <span className="font-mono text-[10.5px] text-faint">
              {ovVisible ? `${ovVisible.nodes.length} nodes · ${ovVisible.edges.length} links` : ""}
              {ov?.truncated ? " · largest shown" : ""}
            </span>
          </>
        ) : (
          <>
            <button
              onClick={() => setMode({ kind: "overview" })}
              className="flex items-center gap-1 rounded px-1.5 py-1 text-[11px] text-muted hover:bg-ink-800 hover:text-parchment"
            >
              <ArrowLeft className="size-3.5" /> Architecture
            </button>
            <KindFilter filters={filters} onChange={setFilters} />
          </>
        )}
        <div className="flex-1" />
        {busy ? <Loader2 className="size-3.5 animate-spin text-lamp" /> : null}
        <NodeSearch repoId={repoId} onPick={explore} />
      </div>

      {mode.kind === "explore" ? (
        <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-ink-700 bg-ink-950/95 px-3 py-1.5 text-[10.5px] text-muted">
          <label className="flex items-center gap-1">
            Direction
            <select
              value={filters.direction}
              onChange={(e) => setFilters({ ...filters, direction: e.target.value as Filters["direction"] })}
              className="rounded border border-ink-700 bg-ink-900 px-1 py-0.5 text-parchment"
            >
              <option value="both">both</option>
              <option value="in">incoming</option>
              <option value="out">outgoing</option>
            </select>
          </label>
          <label className="flex items-center gap-1" title="Only commits, PRs and issues from this window">
            History from
            <YearInput value={filters.since} onChange={(since) => setFilters({ ...filters, since })} />
            to
            <YearInput value={filters.until} onChange={(until) => setFilters({ ...filters, until })} />
          </label>
          <span className="text-faint">Double-click a node to expand it · drag to rearrange</span>
        </div>
      ) : null}

      <div className="relative min-h-0 flex-1">
        {error ? (
          <div className="absolute top-3 left-1/2 z-10 flex -translate-x-1/2 items-center gap-2 rounded border border-danger/40 bg-ink-900 px-3 py-1.5 text-xs text-danger">
            {error}
            <button onClick={() => setError(null)}>
              <X className="size-3" />
            </button>
          </div>
        ) : null}
        {canvas ? (
          <MapCanvas
            nodes={canvas.nodes}
            edges={canvas.edges}
            positions={positions}
            fitKey={mode.kind === "overview" ? ovKey : exFitKey}
            selected={mode.kind === "overview" ? ovSelected : exSelected !== null ? String(exSelected) : null}
            onSelect={(id) => (mode.kind === "overview" ? setOvSelected(id) : setExSelected(id ? Number(id) : null))}
            onActivate={(id) => {
              if (mode.kind === "explore") expand(Number(id));
              else {
                const n = ov?.nodes.find((x) => x.id === id);
                if (n?.kind === "dir" && n.path) {
                  setScope(n.path === "." ? null : n.path);
                  setLevel("file");
                } else if (n) exploreOverviewNode(n);
              }
            }}
            onMove={(id, x, y) =>
              mode.kind === "overview"
                ? setOvDragged({ key: ovKey, pos: new Map(ovPos).set(id, { x, y }) })
                : setExPos((p) => new Map(p).set(id, { x, y }))
            }
          />
        ) : (
          <div className="flex h-full items-center justify-center gap-2 text-sm text-muted">
            <Loader2 className="size-4 animate-spin" /> Mapping the architecture…
          </div>
        )}

        {mode.kind === "overview" && !ovNode && canvas ? <Legend level={level} /> : null}

        {mode.kind === "overview" && ovNode ? (
          <Card onClose={() => setOvSelected(null)}>
            <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-lamp">
              {ovNode.kind === "dir" ? "Directory" : ovNode.kind === "file" ? "File" : ovNode.kind === "dependency" ? "Declared dependency" : "External module"}
            </p>
            <p className="mt-1 font-mono text-sm break-all text-parchment">{ovNode.path ?? ovNode.label}</p>
            {ovNode.kind === "dir" || ovNode.kind === "file" ? (
              <dl className="mt-2 grid grid-cols-3 gap-2 text-center">
                {ovNode.kind === "dir" ? <Stat label="files" value={ovNode.files} /> : null}
                <Stat label="lines" value={ovNode.lines} />
                <Stat label="symbols" value={ovNode.symbols} />
                <Stat label="commits" value={ovNode.commits} />
                <Stat label="depends on" value={ovNode.out} />
                <Stat label="used by" value={ovNode.in} />
              </dl>
            ) : (
              <p className="mt-2 text-xs text-muted">
                Used by {ovNode.users} {level === "dir" ? "directories" : "files"}
                {ovNode.ecosystem ? ` · ${ovNode.ecosystem}` : ""}
              </p>
            )}
            <div className="mt-3 flex flex-wrap gap-1.5">
              {ovNode.kind === "dir" && ovNode.path ? (
                <Action
                  icon={FolderTree}
                  onClick={() => {
                    setScope(ovNode.path === "." ? null : ovNode.path);
                    setLevel("file");
                  }}
                >
                  Show its files
                </Action>
              ) : null}
              {ovNode.kind === "file" && ovNode.path ? (
                <Action icon={Code2} onClick={() => onOpenCode(ovNode.path!)}>
                  Open code
                </Action>
              ) : null}
              {ovNode.kind !== "dir" ? (
                <Action icon={Waypoints} onClick={() => exploreOverviewNode(ovNode)}>
                  Explore connections
                </Action>
              ) : null}
            </div>
          </Card>
        ) : null}

        {mode.kind === "explore" && exNode ? (
          <Card onClose={() => setExSelected(null)}>
            <div className="flex items-center gap-1.5">
              <NodeGlyph node={exNode} />
              <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-lamp">
                {exNode.kind === "symbol" ? String(exNode.data.kind ?? "symbol") : exNode.kind.replace("_", " ")}
                {exNode.id === graph?.root ? " · start" : ""}
              </p>
            </div>
            <p className="mt-1 font-mono text-sm break-all text-parchment">{exNode.label}</p>
            <p className="mt-0.5 truncate font-mono text-[10.5px] text-faint">{nodeSubtitle(exNode)}</p>
            {detail?.id === exNode.id ? (
              <div className="mt-2 flex flex-wrap gap-1">
                {Object.entries(detail.degree).map(([k, d]) => (
                  <span key={k} className="rounded border border-ink-700 px-1.5 py-px font-mono text-[10px] text-muted">
                    {KIND_LABEL[k] ?? k} <span className="text-lamp">←{d.in}</span> <span className="text-evidence">→{d.out}</span>
                  </span>
                ))}
              </div>
            ) : null}
            {graph?.expanded.has(exNode.id) && graph.hidden.get(exNode.id) ? (
              <p className="mt-1.5 text-[10.5px] text-faint">
                {graph.hidden.get(exNode.id)} more neighbours not shown (limit per relation).
              </p>
            ) : null}
            <div className="mt-3 flex flex-wrap gap-1.5">
              {!graph?.expanded.has(exNode.id) ? (
                <Action icon={Network} onClick={() => expand(exNode.id)}>
                  Expand
                </Action>
              ) : null}
              {exNode.path && (exNode.kind === "file" || exNode.kind === "symbol") ? (
                <>
                  <Action icon={Code2} onClick={() => onOpenCode(exNode.path!, exNode.start_line, exNode.end_line)}>
                    Open code
                  </Action>
                  <Action
                    icon={Crosshair}
                    onClick={() =>
                      onInvestigate(
                        exNode.kind === "symbol" && exNode.start_line
                          ? { path: exNode.path!, start_line: exNode.start_line, end_line: exNode.end_line ?? exNode.start_line, label: exNode.label }
                          : { path: exNode.path!, label: exNode.label },
                      )
                    }
                  >
                    Investigate
                  </Action>
                </>
              ) : null}
              {typeof exNode.data.url === "string" ? (
                <a
                  href={exNode.data.url}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-1 rounded border border-ink-700 px-2 py-1 text-[11px] text-muted hover:border-lamp/50 hover:text-parchment"
                >
                  Open on GitHub
                </a>
              ) : null}
              {exNode.id !== graph?.root ? (
                <Action icon={EyeOff} onClick={() => hide(exNode.id)}>
                  Hide
                </Action>
              ) : null}
            </div>
          </Card>
        ) : null}
      </div>
    </div>
  );
}

function toneOf(n: GraphNode): string {
  if (n.kind === "file") return LAMP;
  if (n.kind === "symbol") return n.path && /(^|\/)tests?\//.test(n.path) ? TEAL : "233,228,216";
  if (n.kind === "dependency" || n.kind === "module" || n.kind === "pull_request") return VIOLET;
  if (n.kind === "issue") return TEAL;
  return GREY;
}

function radiusOf(n: GraphNode, root: number): number {
  if (n.id === root) return 20;
  if (n.kind === "file") return 13;
  if (n.kind === "symbol") return String(n.data.kind) === "class" ? 12 : 10;
  if (n.kind === "commit" || n.kind === "author") return 7;
  return 9;
}

function shortLabel(n: GraphNode): string {
  if (n.kind === "commit") return String(n.data.subject ?? n.label).slice(0, 40);
  if (n.kind === "pull_request") return `#${n.key} ${String(n.data.title ?? "")}`.trim();
  if (n.kind === "issue") return `#${n.key} ${String(n.data.title ?? "")}`.trim();
  return n.label;
}

function Segmented<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string; icon: React.ComponentType<{ className?: string }> }[];
}) {
  return (
    <div className="flex rounded-md border border-ink-700 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          onClick={() => onChange(o.value)}
          aria-pressed={value === o.value}
          className={`flex items-center gap-1 rounded px-2 py-0.5 text-[11px] transition-colors ${
            value === o.value ? "bg-lamp-soft text-lamp" : "text-muted hover:text-parchment"
          }`}
        >
          <o.icon className="size-3" />
          {o.label}
        </button>
      ))}
    </div>
  );
}

function KindFilter({ filters, onChange }: { filters: Filters; onChange: (f: Filters) => void }) {
  function toggle(k: string) {
    const kinds = filters.kinds.includes(k) ? filters.kinds.filter((x) => x !== k) : [...filters.kinds, k];
    if (kinds.length) onChange({ ...filters, kinds });
  }
  const chip = (k: string) => (
    <button
      key={k}
      onClick={() => toggle(k)}
      aria-pressed={filters.kinds.includes(k)}
      className={`rounded-full border px-2 py-px text-[10.5px] transition-colors ${
        filters.kinds.includes(k)
          ? "border-lamp/50 bg-lamp-soft text-lamp"
          : "border-ink-700 text-faint hover:text-parchment"
      }`}
    >
      {KIND_LABEL[k]}
    </button>
  );
  return (
    <div className="flex flex-wrap items-center gap-1">
      <span className="font-mono text-[9.5px] uppercase tracking-wider text-faint">code</span>
      {CODE_KINDS.map(chip)}
      <span className="ml-2 font-mono text-[9.5px] uppercase tracking-wider text-faint">history</span>
      {HISTORY_KINDS.map(chip)}
    </div>
  );
}

function YearInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [draft, setDraft] = useState(value);
  return (
    <input
      value={draft}
      inputMode="numeric"
      placeholder="year"
      maxLength={4}
      onChange={(e) => {
        const v = e.target.value.replace(/\D/g, "");
        setDraft(v);
        if (v === "" || v.length === 4) onChange(v);
      }}
      className="w-12 rounded border border-ink-700 bg-ink-900 px-1 py-0.5 text-center font-mono text-parchment outline-none focus:border-lamp/50"
    />
  );
}

function NodeSearch({ repoId, onPick }: { repoId: number; onPick: (id: number) => void }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<{ q: string; hits: GraphNode[] } | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const query = q.trim();
    if (query.length < 2) return;
    let cancelled = false;
    const t = setTimeout(() => {
      api
        .searchGraph(repoId, query)
        .then((hits) => !cancelled && setResults({ q: query, hits }))
        .catch(() => {});
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [q, repoId]);

  const hits = results && results.q === q.trim() ? results.hits : [];
  return (
    <div className="relative">
      <label className="flex items-center gap-1.5 rounded-md border border-ink-700 bg-ink-900 px-2 py-1 focus-within:border-lamp/50">
        <Search className="size-3 text-faint" />
        <input
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          placeholder="Find a function, file, package…"
          aria-label="Find a node to explore"
          className="w-48 bg-transparent text-[11.5px] text-parchment outline-none placeholder:text-faint"
        />
      </label>
      {open && q.trim().length >= 2 ? (
        <ul className="absolute top-full right-0 z-30 mt-1 max-h-80 w-80 overflow-y-auto rounded-lg border border-ink-700 bg-ink-900 p-1 shadow-2xl">
          {hits.length === 0 ? (
            <li className="px-2 py-1.5 text-[11px] text-faint">{results?.q === q.trim() ? "No matches" : "Searching…"}</li>
          ) : (
            hits.map((h) => (
              <li key={h.id}>
                <button
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => {
                    setOpen(false);
                    setQ("");
                    onPick(h.id);
                  }}
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left hover:bg-ink-800"
                >
                  <NodeGlyph node={h} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-mono text-[11.5px] text-parchment">{h.label}</span>
                    <span className="block truncate font-mono text-[10px] text-faint">{nodeSubtitle(h)}</span>
                  </span>
                </button>
              </li>
            ))
          )}
        </ul>
      ) : null}
    </div>
  );
}

function Card({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="absolute top-3 left-3 z-10 w-72 rounded-xl border border-ink-700 bg-ink-900/95 p-3 shadow-2xl backdrop-blur">
      <button onClick={onClose} className="absolute top-2 right-2 rounded p-0.5 text-faint hover:text-parchment" title="Close">
        <X className="size-3.5" />
      </button>
      {children}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number | undefined }) {
  return (
    <div className="rounded border border-ink-700 py-1">
      <dd className="font-mono text-sm text-parchment">{value ?? 0}</dd>
      <dt className="text-[9.5px] text-faint">{label}</dt>
    </div>
  );
}

function Action({
  icon: Icon,
  onClick,
  children,
}: {
  icon: React.ComponentType<{ className?: string }>;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-1 rounded border border-ink-700 px-2 py-1 text-[11px] text-muted transition-colors hover:border-lamp/50 hover:text-parchment"
    >
      <Icon className="size-3" />
      {children}
    </button>
  );
}

function Legend({ level }: { level: "dir" | "file" }) {
  const dot = (rgb: string) => (
    <span className="inline-block size-2.5 rounded-full border" style={{ background: `rgba(${rgb},0.3)`, borderColor: `rgba(${rgb},0.9)` }} />
  );
  const line = (color: string, dashed = false) => (
    <span className="inline-block w-4 border-t-2" style={{ borderColor: color, borderStyle: dashed ? "dashed" : "solid" }} />
  );
  return (
    <div className="pointer-events-none absolute top-3 left-3 z-10 space-y-1 rounded-lg border border-ink-700 bg-ink-900/85 px-3 py-2 text-[10.5px] text-muted backdrop-blur">
      <p className="flex items-center gap-1.5">{dot(LAMP)} {level === "dir" ? "directory" : "file"} · size = lines, brightness = commits</p>
      <p className="flex items-center gap-1.5">{dot(TEAL)} tests</p>
      <p className="flex items-center gap-1.5">{dot(VIOLET)} external package</p>
      <p className="flex items-center gap-1.5">{line(EDGE_COLOR.calls)} calls {line(EDGE_COLOR.imports)} imports {line(`rgba(${VIOLET},0.7)`, true)} uses</p>
      <p className="pt-0.5 text-faint">Click for details · double-click to {level === "dir" ? "open" : "explore"}</p>
    </div>
  );
}
