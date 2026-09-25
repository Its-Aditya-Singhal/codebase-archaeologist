"use client";

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ExternalLink, Loader2 } from "lucide-react";
import { api, type CommitDetail, type Evidence } from "@/lib/api";
import { Diff, formatDate, IssueChip, PrChip, TYPE_ICON } from "./parts";

export function CommitView({
  repoId,
  sha,
  path,
  githubUrl,
}: {
  repoId: number;
  sha: string;
  path: string | null;
  githubUrl: string | null;
}) {
  const [scopeToPath, setScopeToPath] = useState(Boolean(path));
  const [data, setData] = useState<{ key: string; commit: CommitDetail } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const diffPath = scopeToPath ? path : null;
  const key = `${sha}:${diffPath ?? ""}`;

  useEffect(() => {
    let cancelled = false;
    api
      .commit(repoId, sha, diffPath)
      .then((commit) => !cancelled && setData({ key, commit }))
      .catch((e) => !cancelled && setError((e as Error).message));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoId, key]);

  const c = data?.key === key ? data.commit : null;
  if (error) return <p className="p-6 text-sm text-danger">{error}</p>;
  if (!c) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted">
        <Loader2 className="size-4 animate-spin" /> Loading commit…
      </div>
    );
  }
  const Icon = TYPE_ICON.commit;
  return (
    <div className="h-full overflow-y-auto px-6 py-5">
      <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.18em] text-lamp">
        <Icon className="size-3.5" /> Commit
      </div>
      <h2 className="mt-2 text-lg font-medium leading-snug text-parchment">{c.subject}</h2>
      <p className="mt-1 font-mono text-[11px] text-muted">
        {c.sha.slice(0, 10)} · {c.author_name} · {formatDate(c.authored_at)} ·{" "}
        <span className="text-evidence">+{c.insertions}</span>{" "}
        <span className="text-danger">−{c.deletions}</span> in {c.files_changed} files
        {githubUrl ? (
          <a
            href={`${githubUrl}/commit/${c.sha}`}
            target="_blank"
            rel="noreferrer"
            className="ml-2 inline-flex items-center gap-1 text-muted hover:text-lamp"
          >
            <ExternalLink className="size-3" /> GitHub
          </a>
        ) : null}
      </p>
      {c.pull_requests.length || c.issues.length ? (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {c.pull_requests.map((pr) => (
            <PrChip key={pr.number} pr={pr} />
          ))}
          {c.issues.map((i) => (
            <IssueChip key={i.number} issue={i} />
          ))}
        </div>
      ) : null}
      {c.body ? (
        <pre className="mt-4 whitespace-pre-wrap rounded border border-ink-700 bg-ink-900 p-3 font-sans text-[13px] leading-relaxed text-parchment/85">
          {c.body}
        </pre>
      ) : null}
      <div className="mt-5 flex items-center gap-3">
        <h3 className="font-mono text-[10px] uppercase tracking-[0.18em] text-faint">Changes</h3>
        {path ? (
          <div className="flex overflow-hidden rounded border border-ink-700 text-[11px]">
            {[true, false].map((scoped) => (
              <button
                key={String(scoped)}
                onClick={() => setScopeToPath(scoped)}
                className={`px-2 py-0.5 ${scopeToPath === scoped ? "bg-ink-700 text-parchment" : "text-muted hover:text-parchment"}`}
              >
                {scoped ? path.split("/").pop() : `all ${c.files_changed} files`}
              </button>
            ))}
          </div>
        ) : null}
      </div>
      {c.diff ? (
        <Diff text={c.diff} className="mt-2" />
      ) : (
        <p className="mt-2 text-xs text-faint">No textual changes to show.</p>
      )}
    </div>
  );
}

/** A pull request or issue from the evidence list. */
export function RecordView({ evidence: e }: { evidence: Evidence }) {
  const m = e.metadata;
  const Icon = e.source_type === "issue" ? TYPE_ICON.issue : TYPE_ICON.pull_request;
  const label = e.source_type === "issue" ? "Issue" : "Pull request";
  // Content is "PR #n: title\n\nbody"; show the body under our own header.
  const body = e.content.split("\n").slice(1).join("\n").trim();
  return (
    <div className="h-full overflow-y-auto px-6 py-5">
      <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.18em] text-lamp">
        <Icon className="size-3.5" /> {label} #{m.number}
      </div>
      <h2 className="mt-2 text-lg font-medium leading-snug text-parchment">{m.title}</h2>
      <p className="mt-1 font-mono text-[11px] text-muted">
        {m.state} · opened by {m.author} · {formatDate(m.created_at as string)}
        {m.merged_at ? ` · merged ${formatDate(m.merged_at as string)}` : ""}
        {m.url ? (
          <a
            href={m.url as string}
            target="_blank"
            rel="noreferrer"
            className="ml-2 inline-flex items-center gap-1 text-muted hover:text-lamp"
          >
            <ExternalLink className="size-3" /> GitHub
          </a>
        ) : null}
      </p>
      <div className="answer mt-4 rounded border border-ink-700 bg-ink-900 px-4 py-2 text-parchment/85">
        {body ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
        ) : (
          <p className="text-faint">No description.</p>
        )}
      </div>
    </div>
  );
}
