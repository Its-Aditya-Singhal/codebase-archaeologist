"use client";

import { useEffect, useState } from "react";
import { ChevronDown, ExternalLink, Loader2, Sprout } from "lucide-react";
import { api, type Focus, type Timeline, type TimelineCommit } from "@/lib/api";
import { Diff, formatDate, IssueChip, PrChip } from "./parts";

/** How a piece of code evolved: every commit that changed it, oldest marked as
 *  its origin, each with the pull request / issues behind it. */
export function TimelinePanel({
  repoId,
  target,
  onOpenCommit,
}: {
  repoId: number;
  target: Focus;
  onOpenCommit: (sha: string, path: string) => void;
}) {
  const [data, setData] = useState<{ key: string; timeline: Timeline } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = `${target.path}:${target.start_line ?? ""}-${target.end_line ?? ""}`;

  useEffect(() => {
    let cancelled = false;
    api
      .history(repoId, target)
      .then((timeline) => !cancelled && setData({ key, timeline }))
      .catch((e) => !cancelled && setError((e as Error).message));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoId, key]);

  const timeline = data?.key === key ? data.timeline : null;
  if (error) return <p className="p-6 text-sm text-danger">{error}</p>;
  if (!timeline) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted">
        <Loader2 className="size-4 animate-spin" /> Tracing history…
      </div>
    );
  }

  const withPr = timeline.commits.filter((c) => c.pull_requests.length).length;
  const first = timeline.commits.at(-1);
  return (
    <div className="h-full overflow-y-auto">
      <div className="sticky top-0 z-10 border-b border-ink-700 bg-ink-950/95 px-5 py-3 backdrop-blur">
        <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-lamp">
          {timeline.scope === "range" ? "Line history" : "File history"}
        </p>
        <p className="mt-1 truncate font-mono text-xs text-parchment">
          {target.label ?? target.path}
          <span className="text-faint">
            {" "}
            · {timeline.path}
            {timeline.start_line ? `:${timeline.start_line}–${timeline.end_line}` : ""}
          </span>
        </p>
        <p className="mt-1 text-xs text-muted">
          {timeline.commits.length} commits · {timeline.authors.length} authors · {withPr} linked
          to pull requests
          {first ? ` · since ${formatDate(first.date)}` : ""}
        </p>
      </div>
      {timeline.commits.length === 0 ? (
        <p className="p-6 text-sm text-faint">No history found for this code.</p>
      ) : (
        <ol className="relative px-5 py-4">
          <div className="absolute bottom-6 left-[27px] top-6 w-px bg-ink-700" />
          {timeline.commits.map((c) => (
            <TimelineEntry key={c.sha} commit={c} path={timeline.path} onOpenCommit={onOpenCommit} />
          ))}
        </ol>
      )}
    </div>
  );
}

function TimelineEntry({
  commit: c,
  path,
  onOpenCommit,
}: {
  commit: TimelineCommit;
  path: string;
  onOpenCommit: (sha: string, path: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const origin = c.role === "introduced";
  return (
    <li className="relative pb-5 pl-8">
      <span
        className={`absolute left-[3px] top-1.5 flex size-3 items-center justify-center rounded-full border-2 ${
          origin ? "border-lamp bg-lamp" : c.pull_requests.length ? "border-evidence bg-ink-950" : "border-ink-600 bg-ink-950"
        }`}
      />
      <div className="flex flex-wrap items-baseline gap-x-2 font-mono text-[10px] text-faint">
        <span>{formatDate(c.date)}</span>
        <span>{c.author}</span>
        <button onClick={() => onOpenCommit(c.sha, path)} className="text-muted hover:text-lamp">
          {c.sha.slice(0, 10)}
        </button>
        {origin ? (
          <span className="flex items-center gap-1 text-lamp">
            <Sprout className="size-3" /> origin
          </span>
        ) : null}
      </div>
      <p className="mt-0.5 text-[13px] leading-snug text-parchment">{c.subject}</p>
      {c.pull_requests.length || c.issues.length ? (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {c.pull_requests.map((pr) => (
            <PrChip key={pr.number} pr={pr} />
          ))}
          {c.issues.map((i) => (
            <IssueChip key={i.number} issue={i} />
          ))}
        </div>
      ) : null}
      <div className="mt-1.5 flex gap-3 text-[11px]">
        {c.diff ? (
          <button onClick={() => setOpen(!open)} className="flex items-center gap-1 text-muted hover:text-parchment">
            <ChevronDown className={`size-3 transition-transform ${open ? "" : "-rotate-90"}`} />
            {open ? "Hide change" : "Show change"}
          </button>
        ) : null}
        <button
          onClick={() => onOpenCommit(c.sha, path)}
          className="flex items-center gap-1 text-muted hover:text-parchment"
        >
          <ExternalLink className="size-3" /> Commit
        </button>
      </div>
      {open && c.diff ? <Diff text={c.diff} className="mt-2" /> : null}
    </li>
  );
}
