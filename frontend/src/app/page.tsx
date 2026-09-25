"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ArrowRight, GitBranch, Loader2, RotateCw, Trash2 } from "lucide-react";
import { api, IN_PROGRESS, type Repo } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";

const EXAMPLES = ["rq/rq", "pallets/flask", "encode/httpx"];

export default function Home() {
  const [repos, setRepos] = useState<Repo[] | null>(null);
  const [url, setUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setRepos(await api.listRepos());
    } catch {
      setError("Can't reach the API. Is the backend running on port 8000?");
    }
  }, []);

  useEffect(() => {
    api
      .listRepos()
      .then(setRepos)
      .catch(() => setError("Can't reach the API. Is the backend running on port 8000?"));
  }, []);

  // Poll while anything is indexing.
  const indexing = repos?.some((r) => IN_PROGRESS.includes(r.status));
  useEffect(() => {
    if (!indexing) return;
    const t = setInterval(refresh, 2000);
    return () => clearInterval(t);
  }, [indexing, refresh]);

  async function submit(value: string) {
    if (!value.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      await api.createRepo(value.trim());
      setUrl("");
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="survey-grid min-h-full">
      <div className="mx-auto max-w-4xl px-4 py-16 sm:px-6 sm:py-24">
        <p className="font-mono text-xs uppercase tracking-[0.25em] text-lamp">
          Codebase Archaeologist
        </p>
        <h1 className="mt-4 font-display text-5xl leading-[1.05] text-parchment sm:text-6xl">
          Find out <em className="text-lamp">why</em> the code is the way it is.
        </h1>
        <p className="mt-5 max-w-2xl text-base text-muted">
          Point it at a repository. It indexes the source and docs, then answers your questions
          with evidence you can open: file paths, line ranges and cited sources, not guesses.
        </p>

        <form
          className="mt-10 flex flex-col gap-2 sm:flex-row"
          onSubmit={(e) => {
            e.preventDefault();
            submit(url);
          }}
        >
          <div className="flex flex-1 items-center gap-3 rounded-lg border border-ink-700 bg-ink-900 px-4 focus-within:border-lamp/60">
            <GitBranch className="size-4 shrink-0 text-faint" />
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://github.com/owner/repo, owner/repo, or /path/to/checkout"
              className="h-12 w-full bg-transparent font-mono text-sm outline-none placeholder:text-faint"
            />
          </div>
          <button
            type="submit"
            disabled={submitting || !url.trim()}
            className="flex h-12 items-center justify-center gap-2 rounded-lg bg-lamp px-5 text-sm font-medium text-ink-950 transition hover:brightness-110 disabled:opacity-40"
          >
            {submitting ? <Loader2 className="size-4 animate-spin" /> : null}
            Start excavation
          </button>
        </form>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-faint">
          Try
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              onClick={() => setUrl(ex)}
              className="rounded border border-ink-700 px-2 py-0.5 font-mono text-muted hover:border-ink-600 hover:text-parchment"
            >
              {ex}
            </button>
          ))}
        </div>
        {error ? <p className="mt-4 text-sm text-danger">{error}</p> : null}

        <section className="mt-16">
          <h2 className="font-mono text-xs uppercase tracking-[0.2em] text-faint">Sites</h2>
          <div className="mt-4 space-y-2">
            {repos === null ? (
              <p className="text-sm text-faint">Loading…</p>
            ) : repos.length === 0 ? (
              <p className="text-sm text-faint">No repositories yet.</p>
            ) : (
              repos.map((r) => <RepoRow key={r.id} repo={r} onChange={refresh} />)
            )}
          </div>
        </section>
      </div>
    </main>
  );
}

function RepoRow({ repo, onChange }: { repo: Repo; onChange: () => void }) {
  const busy = IN_PROGRESS.includes(repo.status);
  const pct =
    repo.progress.total && repo.progress.done !== undefined
      ? Math.round((repo.progress.done / repo.progress.total) * 100)
      : null;
  const langs = Object.entries(repo.stats.languages ?? {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4);

  const body = (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-3">
          <span className="truncate font-mono text-sm text-parchment">
            {repo.owner ? `${repo.owner}/` : ""}
            <span className="font-semibold">{repo.name}</span>
          </span>
          <StatusBadge status={repo.status} />
        </div>
        {busy ? (
          <div className="mt-2">
            <div className="text-xs text-muted">
              {repo.progress.step ?? "Queued"}
              {pct !== null ? ` · ${repo.progress.done}/${repo.progress.total}` : ""}
            </div>
            <div className="mt-1.5 h-1 overflow-hidden rounded bg-ink-700">
              <div
                className="h-full bg-lamp transition-all"
                style={{ width: `${pct ?? 8}%` }}
              />
            </div>
          </div>
        ) : repo.status === "failed" ? (
          <p className="mt-1 line-clamp-2 text-xs text-danger">{repo.error}</p>
        ) : (
          <p className="mt-1 text-xs text-muted">
            {repo.stats.files} files · {repo.stats.symbols} symbols
            {repo.stats.history?.commits
              ? ` · ${repo.stats.history.commits} commits · ${repo.stats.history.pull_requests ?? 0} PRs`
              : ""}
            {langs.length ? " · " + langs.map(([l]) => l).join(", ") : ""}
          </p>
        )}
      </div>
      <div className="flex items-center gap-1">
        {!busy ? (
          <button
            title="Re-index"
            onClick={async (e) => {
              e.preventDefault();
              await api.reindex(repo.id);
              onChange();
            }}
            className="rounded p-2 text-faint hover:bg-ink-800 hover:text-parchment"
          >
            <RotateCw className="size-4" />
          </button>
        ) : null}
        <button
          title="Remove"
          onClick={async (e) => {
            e.preventDefault();
            if (!confirm(`Remove ${repo.name} and its index?`)) return;
            await api.deleteRepo(repo.id);
            onChange();
          }}
          className="rounded p-2 text-faint hover:bg-ink-800 hover:text-danger"
        >
          <Trash2 className="size-4" />
        </button>
        {repo.status === "ready" ? <ArrowRight className="ml-1 size-4 text-lamp" /> : null}
      </div>
    </div>
  );

  const cls =
    "block rounded-lg border border-ink-700 bg-ink-900/80 px-4 py-3 backdrop-blur transition";
  return repo.status === "ready" ? (
    <Link href={`/repos/${repo.id}`} className={`${cls} hover:border-lamp/50`}>
      {body}
    </Link>
  ) : (
    <div className={cls}>{body}</div>
  );
}
