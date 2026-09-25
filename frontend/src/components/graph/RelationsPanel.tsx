"use client";

import { useEffect, useState } from "react";
import { Loader2, Network, Radar, RefreshCw } from "lucide-react";
import { api, type Focus, type GraphNode, type Impact, type Neighborhood } from "@/lib/api";
import { NeighborhoodGraph } from "./NeighborhoodGraph";
import { ImpactView } from "./ImpactView";

export type RelationsMode = "graph" | "impact";

type Loaded =
  | { key: string; mode: "graph"; data: Neighborhood }
  | { key: string; mode: "impact"; data: Impact }
  | { key: string; mode: RelationsMode; error: string };

/** Knowledge-graph views of the focused code: its neighbourhood (what it calls,
 *  what calls it, where it came from) and the impact of changing it. */
export function RelationsPanel({
  repoId,
  target,
  mode,
  onMode,
  onFocus,
  onOpenCode,
  onOpenCommit,
}: {
  repoId: number;
  target: Focus;
  mode: RelationsMode;
  onMode: (m: RelationsMode) => void;
  onFocus: (f: Focus) => void;
  onOpenCode: (path: string, start?: number | null, end?: number | null) => void;
  onOpenCommit: (sha: string, path: string) => void;
}) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [rebuilding, setRebuilding] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const key = `${mode}:${target.path}:${target.start_line ?? ""}-${target.end_line ?? ""}:${attempt}`;

  useEffect(() => {
    let cancelled = false;
    const done = (l: Loaded) => !cancelled && setLoaded(l);
    const fail = (e: unknown) => done({ key, mode, error: (e as Error).message });
    if (mode === "graph") api.graph(repoId, target).then((data) => done({ key, mode, data }), fail);
    else api.impact(repoId, target).then((data) => done({ key, mode, data }), fail);
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoId, key]);

  const current = loaded?.key === key ? loaded : null;
  const toFocus = (n: { path: string | null; start_line: number | null; end_line: number | null; label: string }) =>
    n.path
      ? onFocus(
          n.start_line && n.label !== n.path
            ? { path: n.path, start_line: n.start_line, end_line: n.end_line ?? n.start_line, label: n.label }
            : { path: n.path, label: n.path.split("/").pop() },
        )
      : undefined;
  const recenter = (n: GraphNode) =>
    toFocus(n.kind === "file" ? { ...n, start_line: null, end_line: null, label: n.path ?? n.label } : n);

  async function rebuild() {
    setRebuilding(true);
    try {
      await api.rebuildGraph(repoId);
      setAttempt((a) => a + 1);
    } finally {
      setRebuilding(false);
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-3 border-b border-ink-700 px-4 py-2">
        <div className="flex overflow-hidden rounded border border-ink-700 text-[11px]">
          {(
            [
              ["graph", Network, "Relations"],
              ["impact", Radar, "Impact"],
            ] as const
          ).map(([m, Icon, label]) => (
            <button
              key={m}
              onClick={() => onMode(m)}
              className={`flex items-center gap-1.5 px-2.5 py-1 ${
                mode === m ? "bg-ink-700 text-parchment" : "text-muted hover:text-parchment"
              }`}
            >
              <Icon className={`size-3.5 ${mode === m ? "text-lamp" : ""}`} /> {label}
            </button>
          ))}
        </div>
        <span className="truncate font-mono text-xs text-parchment">
          {target.label ?? target.path}
          <span className="text-faint"> · {target.path}</span>
        </span>
        <span className="ml-auto hidden shrink-0 text-[10.5px] text-faint xl:block">
          {mode === "graph"
            ? "Click a node to investigate it · ↗ opens the code · dashed = inferred by name"
            : "What could break, and what would tell you"}
        </span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto bg-ink-950 [background-image:radial-gradient(var(--ink-800)_1px,transparent_1px)] [background-size:20px_20px]">
        {!current ? (
          <div className="flex items-center gap-2 p-6 text-sm text-muted">
            <Loader2 className="size-4 animate-spin" /> {mode === "graph" ? "Mapping relations…" : "Tracing dependents…"}
          </div>
        ) : "error" in current ? (
          <div className="max-w-lg p-6 text-sm">
            <p className="text-danger">{current.error}</p>
            {current.error.includes("not been built") ? (
              <button
                onClick={rebuild}
                disabled={rebuilding}
                className="mt-3 flex items-center gap-2 rounded bg-lamp px-3 py-1.5 text-xs font-medium text-ink-950 disabled:opacity-50"
              >
                <RefreshCw className={`size-3.5 ${rebuilding ? "animate-spin" : ""}`} /> Build the knowledge graph
              </button>
            ) : null}
          </div>
        ) : current.mode === "graph" ? (
          current.data.nodes.length <= 1 ? (
            <p className="p-6 text-sm text-faint">
              The static graph has no relations for {current.data.target.label}. It may be unused,
              called only dynamically, or written in a language without call extraction.
            </p>
          ) : (
            <div className="min-w-max py-4">
              <NeighborhoodGraph
                data={current.data}
                onRecenter={recenter}
                onOpenCode={(n) => n.path && onOpenCode(n.path, n.start_line, n.end_line)}
                onOpenCommit={(sha) => onOpenCommit(sha, target.path)}
              />
            </div>
          )
        ) : (
          <ImpactView data={current.data} onOpen={toFocus} />
        )}
      </div>
    </div>
  );
}
