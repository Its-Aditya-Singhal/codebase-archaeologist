"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AlertTriangle, ArrowUp, Crosshair, FileSearch, Square, X, type LucideIcon } from "lucide-react";
import type { Evidence, Focus } from "@/lib/api";
import { formatDate, TYPE_ICON } from "@/components/history/parts";

export interface InvestigationRecord {
  id: string;
  question: string;
  focus: Focus | null;
  evidence: Evidence[];
  answer: string;
  status: "retrieving" | "answering" | "done" | "error";
  error?: string;
}

const SUGGESTIONS_FOCUSED = [
  "Why does this exist?",
  "Who introduced this, and what problem did it solve?",
  "How has this evolved over time?",
  "What calls this, and what would break if I changed it?",
];
const SUGGESTIONS_GLOBAL = [
  "What are the main components of this codebase?",
  "What were the most significant recent changes, and why?",
  "Where is configuration loaded?",
];

export function InvestigationPanel({
  records,
  focus,
  onClearFocus,
  onAsk,
  onStop,
  onOpenEvidence,
}: {
  records: InvestigationRecord[];
  focus: Focus | null;
  onClearFocus: () => void;
  onAsk: (question: string) => void;
  onStop: () => void;
  onOpenEvidence: (e: Evidence) => void;
}) {
  const [question, setQuestion] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const busy = records.some((r) => r.status === "retrieving" || r.status === "answering");
  const latest = records.at(-1);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [records.length, latest?.answer.length]);

  function submit(q: string) {
    if (!q.trim() || busy) return;
    onAsk(q.trim());
    setQuestion("");
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-y-auto px-4 py-4">
        {records.length === 0 ? (
          <div className="mt-6 text-sm text-muted">
            <FileSearch className="mb-3 size-6 text-lamp" />
            <p className="font-display text-2xl text-parchment">Open an investigation</p>
            <p className="mt-2 text-[13px] leading-relaxed">
              Ask about the whole repository, or select a symbol or line range in the code to
              focus the question. Every answer cites the sources it was built from.
            </p>
          </div>
        ) : (
          <div className="space-y-8">
            {records.map((r) => (
              <Record key={r.id} record={r} onOpenEvidence={onOpenEvidence} />
            ))}
          </div>
        )}
        <div ref={bottom} />
      </div>

      <div className="border-t border-ink-700 bg-ink-900 p-3">
        {focus ? (
          <div className="mb-2 flex items-center gap-2 rounded border border-lamp/40 bg-lamp-soft px-2 py-1 text-[11px] text-lamp">
            <Crosshair className="size-3 shrink-0" />
            <span className="truncate font-mono">
              {focus.label} <span className="text-lamp/60">· {focus.path}</span>
            </span>
            <button onClick={onClearFocus} className="ml-auto shrink-0 hover:text-parchment" title="Clear focus">
              <X className="size-3" />
            </button>
          </div>
        ) : null}
        <div className="mb-2 flex flex-wrap gap-1.5">
          {(focus ? SUGGESTIONS_FOCUSED : SUGGESTIONS_GLOBAL).map((s) => (
            <button
              key={s}
              disabled={busy}
              onClick={() => submit(s)}
              className="rounded-full border border-ink-700 px-2.5 py-0.5 text-[11px] text-muted hover:border-lamp/50 hover:text-parchment disabled:opacity-40"
            >
              {s}
            </button>
          ))}
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            submit(question);
          }}
          className="flex items-end gap-2 rounded-lg border border-ink-700 bg-ink-950 p-2 focus-within:border-lamp/50"
        >
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit(question);
              }
            }}
            rows={2}
            placeholder={focus ? `Ask about ${focus.label}…` : "Ask why, where, how…"}
            className="max-h-40 flex-1 resize-none bg-transparent px-1 text-sm outline-none placeholder:text-faint"
          />
          {busy ? (
            <button type="button" onClick={onStop} title="Stop" className="rounded-md bg-ink-700 p-2 text-parchment">
              <Square className="size-3.5" />
            </button>
          ) : (
            <button
              type="submit"
              disabled={!question.trim()}
              title="Investigate"
              className="rounded-md bg-lamp p-2 text-ink-950 disabled:opacity-30"
            >
              <ArrowUp className="size-3.5" />
            </button>
          )}
        </form>
      </div>
    </div>
  );
}

function Record({
  record,
  onOpenEvidence,
}: {
  record: InvestigationRecord;
  onOpenEvidence: (e: Evidence) => void;
}) {
  const [hovered, setHovered] = useState<string | null>(null);
  const byRef = new Map(record.evidence.map((e) => [e.ref, e]));
  const cited = new Set([...record.answer.matchAll(/\[(S\d+)\]/g)].map((m) => m[1]));
  // Turn [S3] into links the markdown renderer hands to our citation chip.
  const linked = record.answer.replace(/\[(S\d+)\]/g, "[$1](#cite-$1)");

  return (
    <article>
      <h3 className="text-[15px] font-medium leading-snug text-parchment">{record.question}</h3>
      {record.focus ? (
        <p className="mt-1 font-mono text-[10px] text-lamp/80">
          focus: {record.focus.label} · {record.focus.path}
        </p>
      ) : null}

      <div className="mt-3">
        {record.status === "retrieving" ? (
          <p className="text-xs text-muted">Searching code, docs and history for evidence…</p>
        ) : null}
        {record.answer ? (
          <div className={`answer text-parchment/90 ${record.status === "answering" ? "streaming-caret" : ""}`}>
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                a: ({ href, children }) => {
                  const ref = href?.startsWith("#cite-") ? href.slice(6) : null;
                  const ev = ref ? byRef.get(ref) : undefined;
                  if (!ref) return <a href={href} className="text-lamp underline">{children}</a>;
                  return (
                    <button
                      onClick={() => ev && onOpenEvidence(ev)}
                      onMouseEnter={() => setHovered(ref)}
                      onMouseLeave={() => setHovered(null)}
                      title={ev ? evidenceTitle(ev).primary : ref}
                      className="mx-0.5 inline-flex -translate-y-px items-center rounded bg-evidence-soft px-1 font-mono text-[10px] leading-4 text-evidence hover:bg-evidence hover:text-ink-950"
                    >
                      {ref}
                    </button>
                  );
                },
              }}
            >
              {linked}
            </ReactMarkdown>
          </div>
        ) : record.status === "answering" ? (
          <p className="streaming-caret text-xs text-muted">Reasoning over {record.evidence.length} sources</p>
        ) : null}
        {record.status === "error" ? (
          <div className="mt-2 flex gap-2 rounded border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
            <AlertTriangle className="mt-px size-3.5 shrink-0" />
            <span>{record.error}</span>
          </div>
        ) : null}
      </div>

      {record.evidence.length ? (
        <details className="group mt-4" open={record.status === "error"}>
          <summary className="cursor-pointer list-none font-mono text-[10px] uppercase tracking-[0.18em] text-faint hover:text-muted">
            Evidence · {record.evidence.length} sources
            {cited.size ? ` · ${cited.size} cited` : ""}
            <span className="ml-1 group-open:hidden">▸</span>
            <span className="ml-1 hidden group-open:inline">▾</span>
          </summary>
          <div className="mt-2 space-y-1">
            {record.evidence.map((e) => (
              <EvidenceRow
                key={e.ref}
                evidence={e}
                cited={cited.has(e.ref)}
                active={hovered === e.ref}
                onOpen={() => onOpenEvidence(e)}
              />
            ))}
          </div>
        </details>
      ) : null}
    </article>
  );
}

function evidenceTitle(e: Evidence): { primary: string; secondary: string } {
  const m = e.metadata ?? {};
  const firstLine = e.content.split("\n")[0];
  if (e.source_type === "commit") {
    return {
      primary: firstLine,
      secondary: `${String(m.sha ?? "").slice(0, 10)} · ${m.author} · ${formatDate(m.date as string)}`,
    };
  }
  if (e.source_type === "graph") {
    return { primary: `Impact of ${e.symbol_name}`, secondary: `dependency graph · ${e.path}` };
  }
  if (e.source_type === "pull_request" || e.source_type === "issue") {
    return {
      primary: `#${m.number} ${m.title ?? ""}`,
      secondary: `${e.source_type === "issue" ? "issue" : "PR"} · ${m.state} · ${m.author}`,
    };
  }
  return {
    primary: `${e.symbol_name ?? e.path?.split("/").pop()}`,
    secondary: `${e.path}:${e.start_line}–${e.end_line}`,
  };
}

function EvidenceRow({
  evidence: e,
  cited,
  active,
  onOpen,
}: {
  evidence: Evidence;
  cited: boolean;
  active: boolean;
  onOpen: () => void;
}) {
  const { primary, secondary } = evidenceTitle(e);
  const Icon = (TYPE_ICON as Record<string, LucideIcon | undefined>)[e.source_type];
  const role = e.metadata?.role as string | undefined;
  const relation = e.metadata?.graph as string | undefined;
  return (
    <button
      onClick={onOpen}
      className={`flex w-full items-start gap-2 rounded border px-2 py-1.5 text-left transition ${
        active ? "border-evidence/60 bg-evidence-soft" : "border-ink-700 hover:border-ink-600 hover:bg-ink-850"
      }`}
    >
      <span
        className={`mt-px shrink-0 rounded px-1 font-mono text-[10px] ${
          cited ? "bg-evidence text-ink-950" : "bg-ink-700 text-muted"
        }`}
      >
        {e.ref}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1 truncate text-[11px] text-parchment">
          {Icon ? <Icon className="size-3 shrink-0 text-lamp" /> : null}
          <span className={`truncate ${Icon ? "" : "font-mono"}`}>{primary}</span>
          {role === "introduced" ? (
            <span className="shrink-0 rounded bg-lamp-soft px-1 font-mono text-[9px] text-lamp">origin</span>
          ) : null}
        </span>
        <span className="block truncate font-mono text-[10px] text-faint" title={relation}>
          {relation ? <span className="text-lamp/80">{relation.split(" (")[0]} · </span> : null}
          {secondary}
        </span>
      </span>
      <span className="shrink-0 font-mono text-[9px] text-faint">{e.matched_by.join(" ")}</span>
    </button>
  );
}
