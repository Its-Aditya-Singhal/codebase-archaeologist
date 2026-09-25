"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowUpRight, Sprout } from "lucide-react";
import type { GraphEdge, GraphNode, Neighborhood } from "@/lib/api";
import { isTestPath, NodeGlyph, nodeSubtitle, RELATION_LABEL, RELATION_TONE } from "./parts";

const CARD_W = 212;
const CARD_H = 44;
const TARGET_H = 104;
const COL_GAP = 76;
const ROW_GAP = 8;
const GROUP_GAP = 22;
const HEAD_H = 20;
const LANE_GAP = 64;
const PAD = 24;

// Column order, left to right. In-side relations sit left of the target,
// out-side relations right of it; depth 2 is the outer column.
const IN_ORDER = ["caller", "importer", "subclass"];
const OUT_ORDER = ["callee", "imports", "base", "uses"];

interface Placed {
  node: GraphNode;
  x: number;
  y: number;
  h: number;
}

interface Group {
  col: number;
  relation: string;
  y: number;
  more: number;
}

function layout(data: Neighborhood) {
  const cols: GraphNode[][][] = [[], [], [], [], []]; // [col][group][node]
  const groupsMeta: { col: number; relation: string }[][] = [[], [], [], [], []];
  const byRel = (side: string, depth: number, rel: string) =>
    data.nodes.filter((n) => n.side === side && n.depth === depth && n.relation === rel);
  const addGroups = (col: number, side: string, depth: number, order: string[]) => {
    for (const rel of order) {
      const nodes = byRel(side, depth, rel);
      if (nodes.length) {
        cols[col].push(nodes);
        groupsMeta[col].push({ col, relation: rel });
      }
    }
  };
  addGroups(0, "in", 2, IN_ORDER);
  addGroups(1, "in", 1, IN_ORDER);
  addGroups(3, "out", 1, OUT_ORDER);
  addGroups(4, "out", 2, OUT_ORDER);

  const history = data.nodes.filter((n) => n.side === "history");
  const hasLane = history.length > 0;
  // A column is shown if it has nodes, or the provenance lane needs it.
  const laneCols = { issue: 0, pull_request: 1, commit: 2 } as Record<string, number>;
  const visible = [0, 1, 2, 3, 4].filter(
    (c) => c === 2 || cols[c].length || history.some((n) => laneCols[n.kind] === c),
  );
  const colX = new Map<number, number>();
  visible.forEach((c, i) => colX.set(c, PAD + i * (CARD_W + COL_GAP)));

  const total = (rel: string, depth: number) => {
    const t = data.totals;
    if (rel === "caller") return depth === 1 ? t.callers : t.callers_depth2;
    if (rel === "callee") return depth === 1 ? t.callees : t.callees_depth2;
    if (rel === "importer") return depth === 1 ? t.importers : t.importers_depth2;
    if (rel === "subclass") return t.subclasses;
    if (rel === "base") return t.bases;
    if (rel === "imports") return t.imports;
    if (rel === "uses") return t.dependencies;
    return 0;
  };

  const heights = cols.map((groups) =>
    groups.reduce((h, g, i) => h + HEAD_H + g.length * (CARD_H + ROW_GAP) + (i ? GROUP_GAP : 0), 0),
  );
  const mainH = Math.max(TARGET_H + 40, ...heights);
  const placed = new Map<number, Placed>();
  const groups: Group[] = [];
  cols.forEach((colGroups, c) => {
    if (!colX.has(c) || c === 2) return;
    let y = PAD + (mainH - heights[c]) / 2;
    colGroups.forEach((nodes, gi) => {
      if (gi) y += GROUP_GAP;
      const { relation } = groupsMeta[c][gi];
      const depth = c === 0 || c === 4 ? 2 : 1;
      groups.push({ col: c, relation, y, more: Math.max(0, (total(relation, depth) ?? 0) - nodes.length) });
      y += HEAD_H;
      for (const n of nodes) {
        placed.set(n.id, { node: n, x: colX.get(c)!, y, h: CARD_H });
        y += CARD_H + ROW_GAP;
      }
    });
  });
  const target = data.nodes.find((n) => n.side === "target")!;
  const targetBox: Placed = {
    node: target,
    x: colX.get(2)!,
    y: PAD + (mainH - TARGET_H) / 2,
    h: TARGET_H,
  };
  placed.set(target.id, targetBox);

  // Provenance lane: one row per commit, its pull request and that PR's issues
  // beside it, so each row reads issue -> PR -> commit.
  let laneTop = 0;
  let bottom = PAD + mainH;
  if (hasLane) {
    laneTop = PAD + mainH + LANE_GAP;
    let cursor = laneTop + HEAD_H;
    const commits = history.filter((n) => n.kind === "commit");
    const edgesFrom = (id: number) => data.edges.filter((e) => e.src === id);
    for (const commit of commits) {
      const rowTop = cursor;
      const stacks = [rowTop, rowTop, rowTop];
      const put = (n: GraphNode, col: number) => {
        if (placed.has(n.id) || !colX.has(col)) return;
        placed.set(n.id, { node: n, x: colX.get(col)!, y: stacks[col], h: CARD_H });
        stacks[col] += CARD_H + ROW_GAP;
      };
      put(commit, 2);
      for (const e of edgesFrom(commit.id)) {
        const pr = history.find((n) => n.id === e.dst && n.kind === "pull_request");
        if (!pr) continue;
        put(pr, 1);
        for (const e2 of edgesFrom(pr.id)) {
          const issue = history.find((n) => n.id === e2.dst && n.kind === "issue");
          if (issue) put(issue, 0);
        }
      }
      cursor = Math.max(...stacks) + 4;
    }
    bottom = cursor;
  }

  const width = PAD * 2 + visible.length * CARD_W + (visible.length - 1) * COL_GAP;
  return { placed, groups, colX, width, height: bottom + PAD, laneTop, targetBox };
}

function edgePath(a: Placed, b: Placed): string {
  // Horizontal flow between columns; vertical spine within the target column.
  if (a.x === b.x) {
    const x = a.x + CARD_W / 2;
    const [top, bot] = a.y < b.y ? [a, b] : [b, a];
    return `M ${x} ${top.y + top.h} L ${x} ${bot.y}`;
  }
  const [l, r] = a.x < b.x ? [a, b] : [b, a];
  const x1 = l.x + CARD_W;
  const y1 = l.y + l.h / 2;
  const x2 = r.x;
  const y2 = r.y + r.h / 2;
  const dx = (x2 - x1) / 2;
  return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
}

export function NeighborhoodGraph({
  data,
  onRecenter,
  onOpenCode,
  onOpenCommit,
}: {
  data: Neighborhood;
  onRecenter: (n: GraphNode) => void;
  onOpenCode: (n: GraphNode) => void;
  onOpenCommit: (sha: string) => void;
}) {
  const { placed, groups, colX, width, height, laneTop, targetBox } = useMemo(
    () => layout(data),
    [data],
  );
  const [hover, setHover] = useState<number | null>(null);
  const targetRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    targetRef.current?.scrollIntoView({ block: "center", inline: "center" });
  }, [data]);

  const drawn = data.edges.filter((e) => placed.has(e.src) && placed.has(e.dst));
  const lit = useMemo(() => {
    if (hover === null) return null;
    const ids = new Set([hover]);
    for (const e of drawn) {
      if (e.src === hover) ids.add(e.dst);
      if (e.dst === hover) ids.add(e.src);
    }
    return ids;
  }, [hover, drawn]);

  function activate(n: GraphNode) {
    if (n.kind === "symbol" || n.kind === "file") onRecenter(n);
    else if (n.kind === "commit") onOpenCommit(n.key);
    else if ((n.kind === "pull_request" || n.kind === "issue") && n.data.url) {
      window.open(String(n.data.url), "_blank", "noreferrer");
    }
  }

  const target = targetBox.node;
  return (
    <div className="relative mx-auto" style={{ width, height }}>
      <svg className="pointer-events-none absolute inset-0" width={width} height={height}>
        <defs>
          {["lamp", "evidence", "violet", "faint"].map((t) => (
            <marker
              key={t}
              id={`arrow-${t}`}
              viewBox="0 0 8 8"
              refX="7"
              refY="4"
              markerWidth="6"
              markerHeight="6"
              orient="auto-start-reverse"
            >
              <path d="M0,0 L8,4 L0,8 z" fill={toneVar(t)} />
            </marker>
          ))}
        </defs>
        {laneTop ? (
          <line
            x1={PAD}
            x2={width - PAD}
            y1={laneTop - LANE_GAP / 2}
            y2={laneTop - LANE_GAP / 2}
            stroke="var(--ink-700)"
            strokeDasharray="2 6"
          />
        ) : null}
        {drawn.map((e, i) => (
          <Edge key={i} edge={e} a={placed.get(e.src)!} b={placed.get(e.dst)!} lit={lit} />
        ))}
      </svg>

      {groups.map((g, i) => (
        <div
          key={i}
          className="absolute flex items-baseline gap-2 font-mono text-[9.5px] uppercase tracking-[0.16em] text-faint"
          style={{ left: colX.get(g.col), top: g.y, width: CARD_W }}
        >
          <span style={{ color: RELATION_TONE[g.relation] }}>{RELATION_LABEL[g.relation] ?? g.relation}</span>
          {g.col === 0 || g.col === 4 ? <span className="normal-case tracking-normal">2 hops</span> : null}
          {g.more ? <span className="ml-auto normal-case tracking-normal">+{g.more} more</span> : null}
        </div>
      ))}
      {laneTop ? (
        <div
          className="absolute font-mono text-[9.5px] uppercase tracking-[0.16em] text-faint"
          style={{ left: PAD, top: laneTop }}
        >
          Provenance · issue → pull request → commit → this code
        </div>
      ) : null}

      {[...placed.values()].map((p) =>
        p.node.id === target.id ? null : (
          <NodeCard
            key={p.node.id}
            p={p}
            edge={drawn.find((e) => e.src === p.node.id || e.dst === p.node.id)}
            dim={lit !== null && !lit.has(p.node.id)}
            onHover={setHover}
            onActivate={activate}
            onOpenCode={onOpenCode}
          />
        ),
      )}
      <TargetCard
        data={data}
        p={targetBox}
        cardRef={targetRef}
        onHover={setHover}
        onOpenCode={onOpenCode}
        onRecenter={onRecenter}
      />
    </div>
  );
}

function toneVar(t: string) {
  return t === "violet" ? "#a996e0" : `var(--${t})`;
}

function Edge({
  edge,
  a,
  b,
  lit,
}: {
  edge: GraphEdge;
  a: Placed;
  b: Placed;
  lit: Set<number> | null;
}) {
  const color = RELATION_TONE[edge.kind] ?? "var(--ink-600)";
  const on = lit === null || (lit.has(edge.src) && lit.has(edge.dst));
  const inferred = edge.confidence < 0.9;
  const code = ["caller", "callee", "importer", "imports", "subclass", "base", "uses"].includes(edge.kind);
  const marker = code
    ? `url(#arrow-${color.includes("lamp") ? "lamp" : color.includes("evidence") ? "evidence" : "violet"})`
    : undefined;
  return (
    <path
      d={edgePath(a, b)}
      fill="none"
      stroke={color}
      strokeWidth={lit && on ? 1.8 : 1.2}
      strokeOpacity={on ? (inferred ? 0.45 : 0.75) : 0.08}
      strokeDasharray={inferred ? "4 4" : undefined}
      markerEnd={a.x < b.x ? marker : undefined}
      markerStart={a.x > b.x ? marker : undefined}
      className="transition-[stroke-opacity] duration-150"
    />
  );
}

function NodeCard({
  p,
  edge,
  dim,
  onHover,
  onActivate,
  onOpenCode,
}: {
  p: Placed;
  edge: GraphEdge | undefined;
  dim: boolean;
  onHover: (id: number | null) => void;
  onActivate: (n: GraphNode) => void;
  onOpenCode: (n: GraphNode) => void;
}) {
  const n = p.node;
  const test = isTestPath(n.path);
  const stdlib = n.kind === "module" && Boolean(n.data.stdlib);
  const origin = n.role === "introduced";
  const inferred = edge && edge.confidence < 0.9;
  const hint = [
    n.label,
    nodeSubtitle(n),
    edge?.via ? `resolved via ${edge.via}` : "",
    edge && edge.confidence < 1 ? `confidence ${edge.confidence}` : "",
    edge && edge.weight > 1 && ["caller", "callee"].includes(edge.kind) ? `${edge.weight} call sites` : "",
  ]
    .filter(Boolean)
    .join("\n");
  return (
    <div
      role="button"
      tabIndex={0}
      title={hint}
      onMouseEnter={() => onHover(n.id)}
      onMouseLeave={() => onHover(null)}
      onClick={() => onActivate(n)}
      onKeyDown={(e) => e.key === "Enter" && onActivate(n)}
      className={`group absolute flex cursor-pointer flex-col justify-center rounded-md border bg-ink-900 px-2.5 transition duration-150 hover:border-parchment/40 hover:bg-ink-850 ${
        origin ? "border-lamp/60" : inferred ? "border-dashed border-ink-600" : "border-ink-700"
      } ${dim ? "opacity-30" : ""} ${test || stdlib ? "text-parchment/60" : ""}`}
      style={{ left: p.x, top: p.y, width: CARD_W, height: p.h }}
    >
      <div className="flex min-w-0 items-center gap-1.5 text-[11.5px]">
        <NodeGlyph node={n} />
        <span className={`truncate ${n.kind === "symbol" || n.kind === "file" ? "font-mono" : ""}`}>
          {n.label}
        </span>
        {origin ? <Sprout className="size-3 shrink-0 text-lamp" /> : null}
        {edge && edge.weight > 1 && ["caller", "callee"].includes(edge.kind) ? (
          <span className="ml-auto shrink-0 font-mono text-[9px] text-faint">×{edge.weight}</span>
        ) : null}
        {n.kind === "symbol" || n.kind === "file" ? (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onOpenCode(n);
            }}
            title="Open in code"
            className={`${edge && edge.weight > 1 ? "" : "ml-auto"} hidden shrink-0 text-faint hover:text-lamp group-hover:block`}
          >
            <ArrowUpRight className="size-3" />
          </button>
        ) : null}
      </div>
      <div className="flex items-center gap-1.5 truncate pl-5 font-mono text-[9.5px] text-faint">
        {test ? <span className="rounded bg-ink-700 px-1 text-[8.5px] uppercase text-muted">test</span> : null}
        {origin ? <span className="text-lamp">origin ·</span> : null}
        <span className="truncate">{nodeSubtitle(n)}</span>
      </div>
    </div>
  );
}

function TargetCard({
  data,
  p,
  cardRef,
  onHover,
  onOpenCode,
  onRecenter,
}: {
  data: Neighborhood;
  p: Placed;
  cardRef: React.RefObject<HTMLDivElement | null>;
  onHover: (id: number | null) => void;
  onOpenCode: (n: GraphNode) => void;
  onRecenter: (n: GraphNode) => void;
}) {
  const t = data.target;
  const kind = t.kind === "symbol" ? String(t.data.kind) : t.kind;
  const counts =
    t.kind === "symbol"
      ? [
          [data.totals.callers, "callers"],
          [data.totals.callees, "calls"],
          [data.totals.dependencies, "packages"],
        ]
      : [
          [data.totals.importers, "importers"],
          [data.totals.imports, "imports"],
          [data.totals.dependencies, "packages"],
        ];
  return (
    <div
      ref={cardRef}
      onMouseEnter={() => onHover(t.id)}
      onMouseLeave={() => onHover(null)}
      className="absolute flex flex-col justify-center rounded-lg border border-lamp/70 bg-ink-850 px-3 shadow-[0_0_0_4px_var(--lamp-soft),0_0_40px_-8px_var(--lamp)]"
      style={{ left: p.x - 10, top: p.y, width: CARD_W + 20, height: p.h }}
    >
      <div className="flex items-center gap-2 font-mono text-[9.5px] uppercase tracking-[0.18em] text-lamp">
        {kind}
        {data.container ? (
          <button
            onClick={() => onRecenter(data.container!)}
            className="truncate normal-case tracking-normal text-faint hover:text-parchment"
            title={`Go to ${data.container.label}`}
          >
            in {data.container.label}
          </button>
        ) : null}
      </div>
      <button
        onClick={() => onOpenCode(t)}
        className="mt-1 truncate text-left font-mono text-[13px] font-semibold text-parchment hover:text-lamp"
        title="Open in code"
      >
        {t.label}
      </button>
      <div className="truncate font-mono text-[9.5px] text-faint">
        {t.path}
        {t.kind === "symbol" ? `:${t.start_line}–${t.end_line}` : ""}
      </div>
      <div className="mt-1.5 flex gap-2.5 font-mono text-[10px] text-muted">
        {counts.map(([n, label]) => (
          <span key={label}>
            <span className="text-parchment">{n ?? 0}</span> {label}
          </span>
        ))}
      </div>
    </div>
  );
}
