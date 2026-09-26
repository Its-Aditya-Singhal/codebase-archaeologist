/** A small force-directed layout (repulsion, springs, gravity, collision), run
 *  synchronously. Enough for the few hundred nodes the map shows at once. */

export interface LayoutNode {
  id: string;
  r: number;
  x?: number;
  y?: number;
  /** Start near this node when it has no position yet. */
  near?: string;
  pinned?: boolean;
}

export interface LayoutLink {
  src: string;
  dst: string;
  strength?: number; // 0..1
}

export type Positions = Map<string, { x: number; y: number }>;

const GOLDEN = Math.PI * (3 - Math.sqrt(5));

export function layout(
  nodes: LayoutNode[],
  links: LayoutLink[],
  { iterations = 300, previous }: { iterations?: number; previous?: Positions } = {},
): Positions {
  const index = new Map(nodes.map((n, i) => [n.id, i]));
  const x = new Float64Array(nodes.length);
  const y = new Float64Array(nodes.length);
  const vx = new Float64Array(nodes.length);
  const vy = new Float64Array(nodes.length);
  const placed = new Uint8Array(nodes.length);

  // Known positions first, then newcomers next to the node they came from,
  // else on a sunflower spiral (deterministic, so layouts don't jump around).
  nodes.forEach((n, i) => {
    const p = previous?.get(n.id) ?? (n.x !== undefined && n.y !== undefined ? { x: n.x, y: n.y } : null);
    if (p) {
      x[i] = p.x;
      y[i] = p.y;
      placed[i] = 1;
    }
  });
  let spiral = 0;
  nodes.forEach((n, i) => {
    if (placed[i]) return;
    const anchor = n.near !== undefined ? index.get(n.near) : undefined;
    const a = (spiral + 1) * GOLDEN;
    if (anchor !== undefined && placed[anchor]) {
      const d = 60 + nodes[anchor].r + n.r;
      x[i] = x[anchor] + Math.cos(a) * d;
      y[i] = y[anchor] + Math.sin(a) * d;
    } else {
      const d = 28 * Math.sqrt(spiral + 1);
      x[i] = Math.cos(a) * d;
      y[i] = Math.sin(a) * d;
    }
    spiral++;
    placed[i] = 1;
  });

  const degree = new Uint16Array(nodes.length);
  const edges = links
    .map((l) => ({ s: index.get(l.src), t: index.get(l.dst), k: l.strength ?? 0.5 }))
    .filter((e): e is { s: number; t: number; k: number } => e.s !== undefined && e.t !== undefined && e.s !== e.t);
  for (const e of edges) {
    degree[e.s]++;
    degree[e.t]++;
  }

  const warm = previous && previous.size ? 0.35 : 1;
  for (let it = 0; it < iterations; it++) {
    const alpha = warm * (1 - it / iterations) ** 1.5 + 0.005;
    // Repulsion and collision between every pair.
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        let dx = x[j] - x[i];
        let dy = y[j] - y[i];
        let d2 = dx * dx + dy * dy;
        if (d2 < 0.01) {
          dx = (i % 7) - 3 + 0.1;
          dy = (j % 5) - 2 + 0.1;
          d2 = dx * dx + dy * dy;
        }
        const d = Math.sqrt(d2);
        const minD = nodes[i].r + nodes[j].r + 14;
        // Repulsion only acts locally, so unconnected nodes don't drift off.
        let f = d < 320 ? (2200 * alpha) / d2 : 0;
        if (d < minD) f += ((minD - d) / d) * 0.5;
        const fx = dx * f;
        const fy = dy * f;
        vx[i] -= fx;
        vy[i] -= fy;
        vx[j] += fx;
        vy[j] += fy;
      }
    }
    // Springs.
    for (const e of edges) {
      const dx = x[e.t] - x[e.s];
      const dy = y[e.t] - y[e.s];
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const rest = 70 + nodes[e.s].r + nodes[e.t].r;
      const f = ((d - rest) / d) * 0.06 * e.k * alpha * 4;
      vx[e.s] += dx * f;
      vy[e.s] += dy * f;
      vx[e.t] -= dx * f;
      vy[e.t] -= dy * f;
    }
    // Gravity to the centre, then integrate with damping.
    for (let i = 0; i < nodes.length; i++) {
      if (nodes[i].pinned) {
        vx[i] = vy[i] = 0;
        continue;
      }
      // Loose nodes are pulled in harder, so they ring the connected core.
      const g = (degree[i] ? 0.012 : 0.03) * Math.max(alpha, 0.2);
      vx[i] -= x[i] * g;
      vy[i] -= y[i] * g;
      x[i] += Math.max(-40, Math.min(40, vx[i]));
      y[i] += Math.max(-40, Math.min(40, vy[i]));
      vx[i] *= 0.55;
      vy[i] *= 0.55;
    }
  }

  const out: Positions = new Map();
  nodes.forEach((n, i) => out.set(n.id, { x: x[i], y: y[i] }));
  return out;
}
