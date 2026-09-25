"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  ArrowRight,
  FileCode2,
  GitBranch,
  GitCommitHorizontal,
  GitPullRequest,
  Loader2,
  Pickaxe,
  RotateCw,
  Trash2,
} from "lucide-react";
import { api, IN_PROGRESS, type Repo } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";
import { UserMenu } from "@/components/auth/UserMenu";
import { useAuth } from "@/components/auth/AuthProvider";
import { Logo } from "@/components/Logo";
import { EASE, GlowCard } from "@/components/motion/primitives";

const EXAMPLES = ["rq/rq", "pallets/flask", "encode/httpx"];

export default function Sites() {
  const { user } = useAuth();
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

  const firstName = user?.name?.split(" ")[0];

  return (
    <div className="grain relative min-h-full overflow-x-clip">
      <div aria-hidden className="survey-grid grid-fade absolute inset-x-0 top-0 h-[520px]" />
      <div
        aria-hidden
        className="breathe absolute -top-40 left-1/2 size-[520px] -translate-x-1/2 rounded-full bg-lamp/10 blur-[110px]"
      />

      <header className="relative z-10 mx-auto flex max-w-5xl items-center justify-between px-4 pt-6 sm:px-6">
        <Logo />
        <UserMenu />
      </header>

      <main className="relative mx-auto max-w-5xl px-4 pt-16 pb-24 sm:px-6 sm:pt-20">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, ease: EASE }}
        >
          <p className="font-mono text-xs uppercase tracking-[0.25em] text-lamp">
            {firstName ? `Welcome back, ${firstName}` : "Your dig sites"}
          </p>
          <h1 className="mt-4 font-display text-4xl leading-[1.05] text-parchment sm:text-6xl">
            Which codebase are we <em className="text-lamp">excavating</em> today?
          </h1>
          <p className="mt-4 max-w-2xl text-muted">
            Add a repository. It indexes the source, docs and history, then answers with evidence
            you can open.
          </p>
        </motion.div>

        <motion.form
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, ease: EASE, delay: 0.1 }}
          className="mt-10 flex flex-col gap-2 sm:flex-row"
          onSubmit={(e) => {
            e.preventDefault();
            submit(url);
          }}
        >
          <label className="group flex flex-1 items-center gap-3 rounded-xl border border-ink-700 bg-ink-900/90 px-4 backdrop-blur transition-all duration-200 focus-within:border-lamp/60 focus-within:shadow-[0_0_0_4px_rgba(233,162,59,0.12)]">
            <GitBranch className="size-4 shrink-0 text-faint transition-colors group-focus-within:text-lamp" />
            <span className="sr-only">Repository</span>
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://github.com/owner/repo, owner/repo, or /path/to/checkout"
              className="h-13 w-full bg-transparent font-mono text-sm outline-none placeholder:text-faint"
            />
          </label>
          <button
            type="submit"
            disabled={submitting || !url.trim()}
            className="btn-shimmer group flex h-13 items-center justify-center gap-2 rounded-xl bg-lamp px-6 text-sm font-semibold text-ink-950 transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {submitting ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Pickaxe className="size-4 transition-transform duration-300 group-hover:-rotate-12" />
            )}
            Start excavation
          </button>
        </motion.form>

        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-faint">
          Try
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              onClick={() => setUrl(ex)}
              className="rounded-md border border-ink-700 px-2 py-1 font-mono text-muted transition-all duration-200 hover:-translate-y-0.5 hover:border-lamp/50 hover:text-parchment"
            >
              {ex}
            </button>
          ))}
        </div>
        <AnimatePresence>
          {error ? (
            <motion.p
              initial={{ opacity: 0, y: -6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              role="alert"
              className="mt-4 text-sm text-danger"
            >
              {error}
            </motion.p>
          ) : null}
        </AnimatePresence>

        <section className="mt-16" aria-labelledby="sites-heading">
          <div className="flex items-baseline justify-between">
            <h2
              id="sites-heading"
              className="font-mono text-xs uppercase tracking-[0.2em] text-faint"
            >
              Sites {repos?.length ? `· ${repos.length}` : ""}
            </h2>
          </div>
          <div className="mt-4">
            {repos === null ? (
              <div className="space-y-3">
                {[0, 1, 2].map((i) => (
                  <div
                    key={i}
                    className="h-20 animate-pulse rounded-xl border border-ink-700 bg-ink-900/60"
                  />
                ))}
              </div>
            ) : repos.length === 0 ? (
              <EmptyState />
            ) : (
              <motion.ul layout className="space-y-3">
                <AnimatePresence initial>
                  {repos.map((r, i) => (
                    <motion.li
                      key={r.id}
                      layout
                      initial={{ opacity: 0, y: 16 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, x: -30 }}
                      transition={{
                        duration: 0.5,
                        ease: EASE,
                        delay: Math.min(i, 8) * 0.06,
                      }}
                    >
                      <RepoRow repo={r} onChange={refresh} />
                    </motion.li>
                  ))}
                </AnimatePresence>
              </motion.ul>
            )}
          </div>
        </section>
      </main>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="rounded-2xl border border-dashed border-ink-700 bg-ink-900/40 px-6 py-14 text-center">
      <span className="mx-auto flex size-12 items-center justify-center rounded-2xl border border-ink-700 bg-ink-850 text-lamp">
        <Pickaxe className="size-5" />
      </span>
      <p className="mt-4 text-parchment">No dig sites yet.</p>
      <p className="mt-1 text-sm text-muted">
        Add a repository above to start your first excavation.
      </p>
    </div>
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
  const h = repo.stats.history;

  const body = (
    <div className="relative z-10 flex flex-col gap-3 sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-3">
          <span className="truncate font-mono text-sm text-parchment">
            {repo.owner ? <span className="text-muted">{repo.owner}/</span> : null}
            <span className="font-semibold">{repo.name}</span>
          </span>
          <StatusBadge status={repo.status} />
        </div>
        {busy ? (
          <div className="mt-2.5">
            <div className="text-xs text-muted">
              {repo.progress.step ?? "Queued"}
              {pct !== null ? ` · ${repo.progress.done}/${repo.progress.total}` : ""}
            </div>
            <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-ink-700">
              <motion.div
                className="progress-stripes h-full rounded-full bg-lamp"
                animate={{ width: `${pct ?? 8}%` }}
                transition={{ duration: 0.6, ease: EASE }}
              />
            </div>
          </div>
        ) : repo.status === "failed" ? (
          <p className="mt-1 line-clamp-2 text-xs text-danger">{repo.error}</p>
        ) : (
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
            <span className="flex items-center gap-1.5">
              <FileCode2 className="size-3.5 text-faint" />
              {repo.stats.files} files · {repo.stats.symbols} symbols
            </span>
            {h?.commits ? (
              <span className="flex items-center gap-1.5">
                <GitCommitHorizontal className="size-3.5 text-faint" />
                {h.commits} commits
              </span>
            ) : null}
            {h?.pull_requests ? (
              <span className="flex items-center gap-1.5">
                <GitPullRequest className="size-3.5 text-faint" />
                {h.pull_requests} PRs
              </span>
            ) : null}
            {langs.length ? (
              <span className="font-mono text-[11px] text-faint">
                {langs.map(([l]) => l).join(" · ")}
              </span>
            ) : null}
          </div>
        )}
      </div>
      <div className="flex items-center gap-1">
        {!busy ? (
          <button
            title="Re-index"
            aria-label={`Re-index ${repo.name}`}
            onClick={async (e) => {
              e.preventDefault();
              await api.reindex(repo.id);
              onChange();
            }}
            className="group/btn flex size-9 items-center justify-center rounded-lg text-faint transition-colors hover:bg-ink-800 hover:text-parchment"
          >
            <RotateCw className="size-4 transition-transform duration-500 group-hover/btn:rotate-180" />
          </button>
        ) : null}
        <button
          title="Remove"
          aria-label={`Remove ${repo.name}`}
          onClick={async (e) => {
            e.preventDefault();
            if (!confirm(`Remove ${repo.name} from your sites?`)) return;
            await api.deleteRepo(repo.id);
            onChange();
          }}
          className="flex size-9 items-center justify-center rounded-lg text-faint transition-colors hover:bg-danger/10 hover:text-danger"
        >
          <Trash2 className="size-4" />
        </button>
        {repo.status === "ready" ? (
          <span className="ml-1 flex size-9 items-center justify-center rounded-lg text-lamp transition-transform duration-300 group-hover:translate-x-1">
            <ArrowRight className="size-4" />
          </span>
        ) : null}
      </div>
    </div>
  );

  const cls =
    "group block rounded-xl border border-ink-700 bg-ink-900/80 px-5 py-4 backdrop-blur transition-all duration-300";
  return repo.status === "ready" ? (
    <GlowCard className="rounded-xl">
      <Link
        href={`/repos/${repo.id}`}
        className={`${cls} hover:-translate-y-0.5 hover:bg-ink-850/90`}
      >
        {body}
      </Link>
    </GlowCard>
  ) : (
    <div className={cls}>{body}</div>
  );
}
