"use client";

import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { codeToTokens, type ThemedToken } from "shiki";
import { ChevronLeft, ChevronRight, GitCommitHorizontal, Loader2, Sprout } from "lucide-react";
import { api, type Evolution, type EvolutionVersion, type Focus } from "@/lib/api";
import { shikiLang } from "@/components/workspace/CodeViewer";
import { EASE } from "@/components/motion/primitives";
import { Diff, formatDate, IssueChip, PrChip } from "./parts";

/** New-file line numbers a unified diff adds or changes. */
function addedLines(diff: string | null): Set<number> {
  const out = new Set<number>();
  let line = 0;
  for (const l of diff?.split("\n") ?? []) {
    const hunk = l.match(/^@@ -\d+(?:,\d+)? \+(\d+)/);
    if (hunk) line = Number(hunk[1]);
    else if (l.startsWith("+")) out.add(line++);
    else if (!l.startsWith("-") && !l.startsWith("\\")) line++;
  }
  return out;
}

/** A definition's versions over time: step through them, oldest first, with
 *  the code as it was, what changed and the pull request behind each change. */
export function EvolutionPanel({
  repoId,
  target,
  onOpenCommit,
}: {
  repoId: number;
  target: Focus;
  onOpenCommit: (sha: string, path: string) => void;
}) {
  const key = `${target.path}:${target.start_line ?? ""}-${target.end_line ?? ""}`;
  const [data, setData] = useState<{ key: string; evo: Evolution } | null>(null);
  const [error, setError] = useState<{ key: string; message: string } | null>(null);
  const [picked, setPicked] = useState<{ key: string; index: number } | null>(null);
  const [view, setView] = useState<"code" | "diff">("code");

  useEffect(() => {
    if (!target.start_line) return;
    let cancelled = false;
    api
      .evolution(repoId, target)
      .then((evo) => !cancelled && setData({ key, evo }))
      .catch((e) => !cancelled && setError({ key, message: (e as Error).message }));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoId, key]);

  const evo = data?.key === key ? data.evo : null;
  const count = evo?.versions.length ?? 0;
  // Default to the newest version; a pick only applies to the target it was made on.
  const index = picked?.key === key ? picked.index : count - 1;
  const version = evo?.versions[index];

  useEffect(() => {
    if (!count) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.closest("input, textarea, [contenteditable]")) return;
      if (e.key === "ArrowLeft" && index > 0) setPicked({ key, index: index - 1 });
      if (e.key === "ArrowRight" && index < count - 1) setPicked({ key, index: index + 1 });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [count, index, key]);

  if (!target.start_line) {
    return (
      <Empty>
        Select a function, class or line range in the code (or click a symbol) to step through
        how it evolved.
      </Empty>
    );
  }
  if (error?.key === key) return <p className="p-6 text-sm text-danger">{error.message}</p>;
  if (!evo) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted">
        <Loader2 className="size-4 animate-spin" /> Reconstructing every version…
      </div>
    );
  }
  if (!version) return <Empty>No commits changed this code, so there is only one version.</Empty>;

  const select = (i: number) => setPicked({ key, index: i });
  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 border-b border-ink-700 bg-ink-950/95 px-5 pt-3 pb-4">
        <div className="flex items-baseline gap-2">
          <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-lamp">Evolution</p>
          <p className="min-w-0 truncate font-mono text-xs text-parchment">
            {evo.symbol ?? target.label ?? evo.path}
            <span className="text-faint"> · {evo.path}</span>
          </p>
        </div>
        <p className="mt-1 text-[11px] text-muted">
          {evo.total_versions} {evo.total_versions === 1 ? "version" : "versions"}
          {evo.truncated ? ` (showing the latest ${count})` : ""} by {evo.authors.length}{" "}
          {evo.authors.length === 1 ? "author" : "authors"}. Use ← → to step through time.
        </p>
        <Stepper versions={evo.versions} index={index} onSelect={select} />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <AnimatePresence mode="wait">
          <motion.div
            key={version.sha}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.25, ease: EASE }}
            className="px-5 py-4"
          >
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-2 text-sm text-parchment">
                  {version.role === "introduced" ? (
                    <span className="flex shrink-0 items-center gap-1 rounded bg-lamp-soft px-1.5 py-px font-mono text-[10px] text-lamp">
                      <Sprout className="size-3" /> origin
                    </span>
                  ) : null}
                  <span className="min-w-0 truncate">{version.subject}</span>
                </p>
                <p className="mt-1 font-mono text-[11px] text-faint">
                  <button
                    onClick={() => onOpenCommit(version.sha, version.path)}
                    className="inline-flex items-center gap-1 text-muted hover:text-lamp"
                    title="Open the full commit"
                  >
                    <GitCommitHorizontal className="size-3" />
                    {version.sha.slice(0, 10)}
                  </button>{" "}
                  · {version.author} · {formatDate(version.date)} ·{" "}
                  <span className="text-evidence">+{version.added}</span>{" "}
                  <span className="text-danger">−{version.removed}</span>
                  {version.path !== evo.path ? ` · then at ${version.path}` : ""}
                </p>
                {version.pull_requests.length || version.issues.length ? (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {version.pull_requests.map((pr) => (
                      <PrChip key={pr.number} pr={pr} />
                    ))}
                    {version.issues.map((is) => (
                      <IssueChip key={is.number} issue={is} />
                    ))}
                  </div>
                ) : null}
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <button
                  disabled={index === 0}
                  onClick={() => select(index - 1)}
                  className="rounded border border-ink-700 p-1 text-muted hover:text-parchment disabled:opacity-30"
                  title="Older version"
                >
                  <ChevronLeft className="size-4" />
                </button>
                <span className="w-14 text-center font-mono text-[11px] text-faint">
                  {index + 1} / {count}
                </span>
                <button
                  disabled={index === count - 1}
                  onClick={() => select(index + 1)}
                  className="rounded border border-ink-700 p-1 text-muted hover:text-parchment disabled:opacity-30"
                  title="Newer version"
                >
                  <ChevronRight className="size-4" />
                </button>
              </div>
            </div>

            <div className="mt-4 flex gap-1 text-[11px]">
              {(["code", "diff"] as const).map((v) => (
                <button
                  key={v}
                  onClick={() => setView(v)}
                  className={`rounded px-2 py-0.5 ${view === v ? "bg-ink-800 text-parchment" : "text-muted hover:text-parchment"}`}
                >
                  {v === "code" ? "Code at this version" : "What changed"}
                </button>
              ))}
            </div>
            {view === "code" ? (
              version.code ? (
                <VersionCode version={version} language={evo.language} />
              ) : (
                <p className="mt-2 text-xs text-muted">The code at this version could not be reconstructed.</p>
              )
            ) : version.diff ? (
              <Diff text={version.diff} className="mt-2" />
            ) : (
              <p className="mt-2 text-xs text-muted">No diff recorded for this version.</p>
            )}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  );
}

function Stepper({
  versions,
  index,
  onSelect,
}: {
  versions: EvolutionVersion[];
  index: number;
  onSelect: (i: number) => void;
}) {
  const last = versions.length - 1;
  return (
    <div className="relative mt-4 px-2">
      <div className="absolute top-[7px] right-2 left-2 h-px bg-ink-700" />
      <motion.div
        className="absolute top-[7px] left-2 h-px origin-left bg-lamp"
        animate={{ width: last ? `calc((100% - 1rem) * ${index / last})` : 0 }}
        transition={{ duration: 0.35, ease: EASE }}
      />
      <ol className="relative flex justify-between">
        {versions.map((v, i) => (
          <li key={v.sha} className="flex flex-col items-center">
            <button
              onClick={() => onSelect(i)}
              title={`${v.subject}\n${v.author} · ${formatDate(v.date)}`}
              aria-label={`Version ${i + 1}: ${v.subject}`}
              aria-current={i === index}
              className={`size-[15px] rounded-full border-2 transition-all duration-200 ${
                i === index
                  ? "scale-125 border-lamp bg-lamp shadow-[0_0_12px_2px_rgba(233,162,59,0.45)]"
                  : i < index
                    ? "border-lamp/70 bg-ink-900 hover:bg-lamp/40"
                    : "border-ink-600 bg-ink-900 hover:border-lamp/60"
              }`}
            />
            {versions.length <= 12 || i === 0 || i === last || i === index ? (
              <span className={`mt-1.5 font-mono text-[9.5px] ${i === index ? "text-lamp" : "text-faint"}`}>
                {v.date.slice(0, 4)}
              </span>
            ) : null}
          </li>
        ))}
      </ol>
    </div>
  );
}

function VersionCode({ version, language }: { version: EvolutionVersion; language: string | null }) {
  const [tokens, setTokens] = useState<{ sha: string; lines: ThemedToken[][] } | null>(null);
  const code = version.code ?? "";
  const changed = useMemo(() => addedLines(version.diff), [version.diff]);
  const start = version.start_line ?? 1;

  useEffect(() => {
    let cancelled = false;
    codeToTokens(code, { lang: shikiLang(language) as never, theme: "vitesse-dark" })
      .then((r) => !cancelled && setTokens({ sha: version.sha, lines: r.tokens }))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [code, language, version.sha]);

  const lines = tokens?.sha === version.sha ? tokens.lines : null;
  return (
    <pre className="mt-2 overflow-x-auto rounded border border-ink-700 bg-ink-950 py-2 font-mono text-[12px] leading-[1.6]">
      {code.split("\n").map((plain, i) => {
        const n = start + i;
        const isNew = changed.has(n);
        return (
          <div key={i} className={`flex pr-4 ${isNew ? "bg-evidence/10" : ""}`}>
            <span className={`w-12 shrink-0 select-none pr-3 text-right ${isNew ? "text-evidence" : "text-ink-600"}`}>
              {n}
            </span>
            <span className="whitespace-pre">
              {lines?.[i]
                ? lines[i].map((t, j) => (
                    <span key={j} style={{ color: t.color }}>
                      {t.content}
                    </span>
                  ))
                : plain || " "}
            </span>
          </div>
        );
      })}
    </pre>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="flex h-full items-center justify-center p-8 text-center text-sm text-faint">{children}</div>;
}
