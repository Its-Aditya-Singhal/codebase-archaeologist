"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Maximize2, Minus, Plus } from "lucide-react";
import type { Positions } from "./forceLayout";

export interface CanvasNode {
  id: string;
  label: string;
  r: number;
  fill: string;
  stroke: string;
  /** Short text inside the circle (a glyph or count). */
  glyph?: string;
  /** Always show the label (otherwise only when zoomed in, hovered or selected). */
  prominent?: boolean;
  title?: string;
}

export interface CanvasEdge {
  id: string;
  src: string;
  dst: string;
  width: number;
  color: string;
  dashed?: boolean;
  title?: string;
}

interface View {
  x: number;
  y: number;
  k: number;
}

/** An SVG graph you can pan (drag the background), zoom (wheel / pinch, or the
 *  buttons) and rearrange (drag a node). Clicking a node selects it. */
export function MapCanvas({
  nodes,
  edges,
  positions,
  selected,
  onSelect,
  onActivate,
  onMove,
  fitKey,
}: {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  positions: Positions;
  selected: string | null;
  onSelect: (id: string | null) => void;
  /** Double-click. */
  onActivate?: (id: string) => void;
  onMove: (id: string, x: number, y: number) => void;
  /** Changing this re-fits the view to the graph. */
  fitKey: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 800, h: 600 });
  const [view, setView] = useState<View>({ x: 400, y: 300, k: 1 });
  const [hover, setHover] = useState<string | null>(null);
  const drag = useRef<
    | { kind: "pan"; sx: number; sy: number; vx: number; vy: number; moved: boolean }
    | { kind: "node"; id: string; sx: number; sy: number; moved: boolean }
    | null
  >(null);
  // Double-clicks are detected here: pointer capture retargets the native event.
  const lastClick = useRef<{ id: string; at: number } | null>(null);

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setSize({ w: e.contentRect.width, h: e.contentRect.height }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);

  const fit = useCallback(() => {
    if (!nodes.length) return;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of nodes) {
      const p = positions.get(n.id);
      if (!p) continue;
      x0 = Math.min(x0, p.x - n.r);
      y0 = Math.min(y0, p.y - n.r);
      x1 = Math.max(x1, p.x + n.r);
      y1 = Math.max(y1, p.y + n.r + 16);
    }
    if (!Number.isFinite(x0)) return;
    const pad = 48;
    const k = Math.min(2, (size.w - pad * 2) / Math.max(1, x1 - x0), (size.h - pad * 2) / Math.max(1, y1 - y0));
    setView({ k, x: size.w / 2 - ((x0 + x1) / 2) * k, y: size.h / 2 - ((y0 + y1) / 2) * k });
  }, [nodes, positions, size]);

  // Re-fit when the graph is replaced or the panel resizes; not on every drag.
  const fitRef = useRef(fit);
  useEffect(() => {
    fitRef.current = fit;
  });
  useEffect(() => {
    fitRef.current();
  }, [fitKey, size.w, size.h]);

  const zoomAt = useCallback((factor: number, cx: number, cy: number) => {
    setView((v) => {
      const k = Math.min(4, Math.max(0.15, v.k * factor));
      const f = k / v.k;
      return { k, x: cx - (cx - v.x) * f, y: cy - (cy - v.y) * f };
    });
  }, []);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      zoomAt(Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015)), e.clientX - r.left, e.clientY - r.top);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomAt]);

  function toWorld(clientX: number, clientY: number) {
    const r = box.current!.getBoundingClientRect();
    return { x: (clientX - r.left - view.x) / view.k, y: (clientY - r.top - view.y) / view.k };
  }

  function onPointerDown(e: React.PointerEvent) {
    const nodeId = (e.target as Element).closest<SVGGElement>("[data-node]")?.dataset.node;
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    drag.current = nodeId
      ? { kind: "node", id: nodeId, sx: e.clientX, sy: e.clientY, moved: false }
      : { kind: "pan", sx: e.clientX, sy: e.clientY, vx: view.x, vy: view.y, moved: false };
  }

  function onPointerMove(e: React.PointerEvent) {
    const d = drag.current;
    if (!d) return;
    if (Math.abs(e.clientX - d.sx) + Math.abs(e.clientY - d.sy) > 3) d.moved = true;
    if (!d.moved) return;
    if (d.kind === "pan") {
      setView((v) => ({ ...v, x: d.vx + e.clientX - d.sx, y: d.vy + e.clientY - d.sy }));
    } else {
      const p = toWorld(e.clientX, e.clientY);
      onMove(d.id, p.x, p.y);
    }
  }

  function onPointerUp() {
    const d = drag.current;
    drag.current = null;
    if (!d || d.moved) return;
    if (d.kind === "node") {
      const now = performance.now();
      const prev = lastClick.current;
      lastClick.current = { id: d.id, at: now };
      if (prev && prev.id === d.id && now - prev.at < 350) {
        lastClick.current = null;
        onActivate?.(d.id);
        return;
      }
    }
    onSelect(d.kind === "node" ? d.id : null);
  }

  // What to emphasise: the hovered (else selected) node and its neighbours.
  const lit = hover ?? selected;
  const neighbours = useMemo(() => {
    if (!lit) return null;
    const s = new Set([lit]);
    for (const e of edges) {
      if (e.src === lit) s.add(e.dst);
      if (e.dst === lit) s.add(e.src);
    }
    return s;
  }, [lit, edges]);

  return (
    <div
      ref={box}
      className="relative h-full w-full touch-none overflow-hidden bg-ink-950 select-none"
      style={{
        backgroundImage: "radial-gradient(circle, rgba(233,228,216,0.05) 1px, transparent 1px)",
        backgroundSize: `${24 * view.k}px ${24 * view.k}px`,
        backgroundPosition: `${view.x}px ${view.y}px`,
      }}
    >
      <svg
        className="h-full w-full cursor-grab active:cursor-grabbing"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={() => (drag.current = null)}
        role="img"
        aria-label={`Graph with ${nodes.length} nodes and ${edges.length} connections`}
      >
        <defs>
          <marker id="map-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill="context-stroke" />
          </marker>
        </defs>
        <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
          {edges.map((e) => {
            const a = positions.get(e.src);
            const b = positions.get(e.dst);
            const nb = byId.get(e.dst);
            if (!a || !b || !nb) return null;
            const dx = b.x - a.x;
            const dy = b.y - a.y;
            const d = Math.sqrt(dx * dx + dy * dy) || 1;
            // Stop the arrow at the target's rim; bow the line slightly so
            // edges in both directions stay apart.
            const ex = b.x - (dx / d) * (nb.r + 3);
            const ey = b.y - (dy / d) * (nb.r + 3);
            const mx = (a.x + ex) / 2 - (dy / d) * d * 0.08;
            const my = (a.y + ey) / 2 + (dx / d) * d * 0.08;
            const on = !neighbours || (neighbours.has(e.src) && neighbours.has(e.dst) && (e.src === lit || e.dst === lit));
            return (
              <path
                key={e.id}
                d={`M${a.x},${a.y} Q${mx},${my} ${ex},${ey}`}
                fill="none"
                stroke={e.color}
                strokeWidth={e.width / Math.sqrt(view.k)}
                strokeDasharray={e.dashed ? "4 4" : undefined}
                markerEnd="url(#map-arrow)"
                opacity={on ? (neighbours ? 0.95 : 0.5) : 0.07}
                className="transition-opacity duration-200"
              >
                {e.title ? <title>{e.title}</title> : null}
              </path>
            );
          })}
          {nodes.map((n) => {
            const p = positions.get(n.id);
            if (!p) return null;
            const on = !neighbours || neighbours.has(n.id);
            const isSel = n.id === selected;
            const showLabel = n.prominent || isSel || n.id === hover || view.k > 1.3 || (neighbours?.has(n.id) ?? false);
            return (
              <g
                key={n.id}
                data-node={n.id}
                transform={`translate(${p.x},${p.y})`}
                onPointerEnter={() => setHover(n.id)}
                onPointerLeave={() => setHover((h) => (h === n.id ? null : h))}
                opacity={on ? 1 : 0.18}
                className="cursor-pointer transition-opacity duration-200"
              >
                {isSel ? <circle r={n.r + 6} fill="none" stroke="var(--lamp)" strokeWidth={1.5} opacity={0.6} className="animate-pulse" /> : null}
                <circle r={n.r} fill={n.fill} stroke={isSel ? "var(--lamp)" : n.stroke} strokeWidth={isSel ? 2.5 : 1.5} />
                {n.glyph && n.r >= 9 ? (
                  <text textAnchor="middle" dy="0.35em" fontSize={Math.min(12, n.r * 0.8)} className="pointer-events-none fill-parchment font-mono">
                    {n.glyph}
                  </text>
                ) : null}
                {showLabel ? (
                  <text
                    y={n.r + 12 / Math.sqrt(view.k)}
                    textAnchor="middle"
                    fontSize={11 / Math.sqrt(view.k)}
                    className="pointer-events-none fill-parchment font-mono"
                    style={{ paintOrder: "stroke", stroke: "var(--ink-950)", strokeWidth: 3 / Math.sqrt(view.k) }}
                  >
                    {n.label.length > 32 ? `${n.label.slice(0, 30)}…` : n.label}
                  </text>
                ) : null}
                {n.title ? <title>{n.title}</title> : null}
              </g>
            );
          })}
        </g>
      </svg>

      <div className="absolute right-3 bottom-3 flex flex-col overflow-hidden rounded-lg border border-ink-700 bg-ink-900/90 backdrop-blur">
        <button onClick={() => zoomAt(1.3, size.w / 2, size.h / 2)} className="p-1.5 text-muted hover:bg-ink-800 hover:text-parchment" title="Zoom in">
          <Plus className="size-3.5" />
        </button>
        <button onClick={() => zoomAt(1 / 1.3, size.w / 2, size.h / 2)} className="border-t border-ink-700 p-1.5 text-muted hover:bg-ink-800 hover:text-parchment" title="Zoom out">
          <Minus className="size-3.5" />
        </button>
        <button onClick={fit} className="border-t border-ink-700 p-1.5 text-muted hover:bg-ink-800 hover:text-parchment" title="Fit to view">
          <Maximize2 className="size-3.5" />
        </button>
      </div>
    </div>
  );
}
