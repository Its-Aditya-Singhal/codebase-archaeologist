"use client";

import { AnimatePresence, motion } from "motion/react";
import {
  Box,
  CircleSlash,
  FileCode2,
  GitPullRequest,
  History,
  Loader2,
  Network,
  Radar,
  Search,
  type LucideIcon,
} from "lucide-react";
import type { AgentStep, Evidence } from "@/lib/api";
import { EASE } from "@/components/motion/primitives";

const TOOL: Record<string, { icon: LucideIcon; label: string }> = {
  search: { icon: Search, label: "Searched" },
  read_code: { icon: FileCode2, label: "Read" },
  find_symbol: { icon: Box, label: "Looked up" },
  code_history: { icon: History, label: "Traced history of" },
  relations: { icon: Network, label: "Followed relations of" },
  impact: { icon: Radar, label: "Assessed impact of" },
  open_record: { icon: GitPullRequest, label: "Opened" },
};

/** What the tool was pointed at, in a few words. */
function target(step: AgentStep): string {
  const a = step.input as Record<string, string | number | undefined>;
  if (a.query) return `“${a.query}”`;
  if (a.name) return String(a.name);
  if (a.path) {
    const where = a.start_line ? `:${a.start_line}${a.end_line ? `–${a.end_line}` : ""}` : "";
    return `${a.path}${where}`;
  }
  if (a.kind && a.id) return `${String(a.kind).replace("_", " ")} ${a.id}`;
  return "";
}

/** The agent's evidence trail: each tool call as it happens, with the sources it added. */
export function AgentTrail({
  steps,
  running,
  evidence,
  onOpenEvidence,
}: {
  steps: AgentStep[];
  running: boolean;
  evidence: Map<string, Evidence>;
  onOpenEvidence: (e: Evidence) => void;
}) {
  const added = steps.reduce((n, s) => n + s.added.length, 0);
  const list = (
    <ol className="relative mt-2 space-y-1.5 border-l border-ink-700 pl-3">
      <AnimatePresence initial={false}>
        {steps.map((s) => {
          const tool = s.tool ? TOOL[s.tool] : null;
          const Icon = tool?.icon ?? CircleSlash;
          return (
            <motion.li
              key={s.n + (s.tool ?? "")}
              initial={{ opacity: 0, x: -6 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.3, ease: EASE }}
              className="relative"
            >
              <span className="absolute top-1 -left-[17px] size-2 rounded-full border border-ink-600 bg-ink-900" />
              <div className="flex items-start gap-1.5 text-[11.5px] leading-snug">
                <Icon className={`mt-0.5 size-3 shrink-0 ${tool ? "text-evidence" : "text-faint"}`} />
                <div className="min-w-0 flex-1">
                  <span className="text-parchment">
                    {tool ? `${tool.label} ` : ""}
                    <span className="break-all font-mono text-[11px] text-parchment/85">{target(s)}</span>
                  </span>
                  <span className="text-faint"> · {s.summary}</span>
                  {s.added.length ? (
                    <span className="ml-1 inline-flex flex-wrap gap-0.5 align-middle">
                      {s.added.map((ref) => {
                        const ev = evidence.get(ref);
                        return (
                          <button
                            key={ref}
                            disabled={!ev}
                            onClick={() => ev && onOpenEvidence(ev)}
                            className="rounded bg-evidence-soft px-1 font-mono text-[9.5px] leading-4 text-evidence enabled:hover:bg-evidence enabled:hover:text-ink-950"
                          >
                            {ref}
                          </button>
                        );
                      })}
                    </span>
                  ) : null}
                </div>
              </div>
            </motion.li>
          );
        })}
      </AnimatePresence>
      {running ? (
        <li className="relative flex items-center gap-1.5 text-[11.5px] text-muted">
          <span className="absolute top-1 -left-[17px] size-2 animate-ping rounded-full bg-evidence/60" />
          <Loader2 className="size-3 animate-spin text-evidence" />
          {steps.length ? "Deciding the next step…" : "Planning the investigation…"}
        </li>
      ) : null}
    </ol>
  );

  if (running) {
    return (
      <div className="mt-3 rounded-lg border border-evidence/25 bg-evidence-soft/30 px-3 py-2">
        <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-evidence">
          Agent at work · {steps.length} {steps.length === 1 ? "step" : "steps"}
        </p>
        {list}
      </div>
    );
  }
  return (
    <details className="group mt-3 rounded-lg border border-ink-700 px-3 py-2">
      <summary className="cursor-pointer list-none font-mono text-[10px] uppercase tracking-[0.18em] text-faint hover:text-muted">
        Agent trail · {steps.length} {steps.length === 1 ? "step" : "steps"} · {added} sources
        gathered
        <span className="ml-1 group-open:hidden">▸</span>
        <span className="ml-1 hidden group-open:inline">▾</span>
      </summary>
      {list}
    </details>
  );
}
