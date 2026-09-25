import { CircleDot, GitCommitHorizontal, GitPullRequest, Radar } from "lucide-react";
import type { IssueRef, PullRequestRef } from "@/lib/api";

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export const TYPE_ICON = {
  commit: GitCommitHorizontal,
  pull_request: GitPullRequest,
  issue: CircleDot,
  graph: Radar,
} as const;

const STATE_TONE: Record<string, string> = {
  merged: "text-violet-300 border-violet-400/40",
  open: "text-evidence border-evidence/40",
  closed: "text-muted border-ink-600",
};

export function PrChip({ pr }: { pr: PullRequestRef }) {
  const tone = STATE_TONE[pr.state ?? ""] ?? "text-muted border-ink-600";
  const body = (
    <>
      <GitPullRequest className="size-3 shrink-0" />#{pr.number}
      {pr.title ? <span className="max-w-64 truncate text-parchment/80">{pr.title}</span> : null}
    </>
  );
  const cls = `inline-flex items-center gap-1 rounded border px-1.5 py-px font-mono text-[10px] ${tone}`;
  return pr.url ? (
    <a href={pr.url} target="_blank" rel="noreferrer" className={`${cls} hover:bg-ink-800`}>
      {body}
    </a>
  ) : (
    <span className={cls} title="Pull request not fetched from GitHub yet">
      {body}
    </span>
  );
}

export function IssueChip({ issue }: { issue: IssueRef }) {
  const tone = STATE_TONE[issue.state] ?? "text-muted border-ink-600";
  return (
    <a
      href={issue.url}
      target="_blank"
      rel="noreferrer"
      title={`${issue.kind} · ${issue.title}`}
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-px font-mono text-[10px] hover:bg-ink-800 ${tone}`}
    >
      <CircleDot className="size-3 shrink-0" />#{issue.number}
      <span className="max-w-56 truncate text-parchment/80">{issue.title}</span>
    </a>
  );
}

/** Unified diff with +/- colouring. */
export function Diff({ text, className = "" }: { text: string; className?: string }) {
  return (
    <pre
      className={`overflow-x-auto rounded border border-ink-700 bg-ink-950 py-2 font-mono text-[11.5px] leading-[1.55] ${className}`}
    >
      {text.split("\n").map((line, i) => {
        const tone = line.startsWith("+")
          ? "bg-evidence/10 text-evidence"
          : line.startsWith("-")
            ? "bg-danger/10 text-danger"
            : line.startsWith("@@")
              ? "text-lamp/70"
              : line.startsWith("diff --git")
                ? "mt-2 border-t border-ink-700 pt-2 text-parchment"
                : "text-muted";
        return (
          <div key={i} className={`px-3 ${tone}`}>
            {line || " "}
          </div>
        );
      })}
    </pre>
  );
}
