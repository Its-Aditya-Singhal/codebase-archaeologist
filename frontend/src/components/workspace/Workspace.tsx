"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, Code2, GitCommitHorizontal, History, TriangleAlert } from "lucide-react";
import { api, ask, type Evidence, type FileDetail, type Focus, type Repo, type RepoFile } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";
import { FileExplorer } from "./FileExplorer";
import { CodeViewer, type Highlight } from "./CodeViewer";
import { InvestigationPanel, type InvestigationRecord } from "./Investigation";
import { TimelinePanel } from "@/components/history/TimelinePanel";
import { CommitView, RecordView } from "@/components/history/RecordViews";

/** What the centre panel shows. */
type CenterView =
  | { kind: "code" }
  | { kind: "history" }
  | { kind: "commit"; sha: string; path: string | null }
  | { kind: "record"; evidence: Evidence };

export function Workspace({ repoId }: { repoId: number }) {
  const [repo, setRepo] = useState<Repo | null>(null);
  const [files, setFiles] = useState<RepoFile[]>([]);
  const [file, setFile] = useState<FileDetail | null>(null);
  const [highlight, setHighlight] = useState<Highlight | null>(null);
  const [focus, setFocus] = useState<Focus | null>(null);
  const [records, setRecords] = useState<InvestigationRecord[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [view, setView] = useState<CenterView>({ kind: "code" });
  const abort = useRef<AbortController | null>(null);

  const currentPath = useRef<string | null>(null);
  const openFile = useCallback(
    async (path: string, hl: Highlight | null = null) => {
      setView({ kind: "code" });
      setHighlight(hl);
      if (currentPath.current === path) return;
      currentPath.current = path;
      try {
        const detail = await api.getFile(repoId, path);
        if (currentPath.current === path) setFile(detail);
      } catch {
        currentPath.current = null;
      }
    },
    [repoId],
  );

  useEffect(() => {
    Promise.all([api.getRepo(repoId), api.listFiles(repoId)])
      .then(([r, f]) => {
        setRepo(r);
        setFiles(f);
        const readme = f.find((x) => /^readme\.(md|rst|txt)$/i.test(x.path));
        if (readme) openFile(readme.path);
      })
      .catch((e) => setLoadError((e as Error).message));
  }, [repoId, openFile]);

  function openEvidence(e: Evidence) {
    if (e.source_type === "commit" && e.metadata.sha) {
      setView({ kind: "commit", sha: String(e.metadata.sha), path: e.path });
    } else if (e.source_type === "pull_request" || e.source_type === "issue") {
      setView({ kind: "record", evidence: e });
    } else if (e.path) {
      openFile(
        e.path,
        e.start_line ? { start: e.start_line, end: e.end_line ?? e.start_line, tone: "evidence" } : null,
      );
    }
  }

  // History follows the focus when it is in the open file, else the whole file.
  const historyTarget: Focus | null =
    focus && (!file || focus.path === file.path)
      ? focus
      : file
        ? { path: file.path, label: file.path.split("/").pop() }
        : null;
  const githubUrl = repo?.url.startsWith("https://github.com/") ? repo.url : null;
  const h = repo?.stats.history;

  function update(id: string, patch: (r: InvestigationRecord) => Partial<InvestigationRecord>) {
    setRecords((rs) => rs.map((r) => (r.id === id ? { ...r, ...patch(r) } : r)));
  }

  async function investigate(question: string) {
    const id = crypto.randomUUID();
    const askedFocus = focus;
    setRecords((rs) => [...rs, { id, question, focus: askedFocus, evidence: [], answer: "", status: "retrieving" }]);
    abort.current = new AbortController();
    try {
      for await (const ev of ask(repoId, question, askedFocus, abort.current.signal)) {
        if (ev.event === "sources") update(id, () => ({ evidence: ev.data, status: "answering" }));
        else if (ev.event === "delta") update(id, (r) => ({ answer: r.answer + ev.data.text }));
        else if (ev.event === "done") update(id, () => ({ status: "done" }));
        else if (ev.event === "error") update(id, () => ({ status: "error", error: ev.data.message }));
      }
      update(id, (r) => (r.status === "answering" ? { status: "done" } : {}));
    } catch (e) {
      const aborted = (e as Error).name === "AbortError";
      update(id, (r) => ({
        status: aborted ? "done" : "error",
        error: aborted ? undefined : (e as Error).message,
        answer: aborted && r.answer ? r.answer + "\n\n*(stopped)*" : r.answer,
      }));
    }
  }

  if (loadError) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-danger">{loadError}</div>
    );
  }

  return (
    <div className="flex h-full flex-col bg-ink-950">
      <header className="flex h-11 shrink-0 items-center gap-3 border-b border-ink-700 bg-ink-900 px-3">
        <Link href="/" className="rounded p-1 text-faint hover:bg-ink-800 hover:text-parchment" title="All sites">
          <ArrowLeft className="size-4" />
        </Link>
        <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-lamp">Archaeologist</span>
        <span className="text-ink-600">/</span>
        <span className="truncate font-mono text-sm">
          {repo?.owner ? `${repo.owner}/` : ""}
          <span className="font-semibold">{repo?.name}</span>
        </span>
        {repo ? <StatusBadge status={repo.status} /> : null}
        {repo?.head_sha ? (
          <span className="hidden items-center gap-1 font-mono text-[11px] text-faint md:flex">
            <GitCommitHorizontal className="size-3.5" />
            {repo.default_branch} @ {repo.head_sha.slice(0, 7)}
          </span>
        ) : null}
        {repo?.stats.files ? (
          <span className="ml-auto hidden items-center gap-2 font-mono text-[11px] text-faint lg:flex">
            {repo.stats.files} files · {repo.stats.symbols} symbols
            {h?.commits ? ` · ${h.commits} commits · ${h.pull_requests ?? 0} PRs · ${h.issues ?? 0} issues` : ""}
            {h?.github && !h.github.complete && h.github.note ? (
              <span title={h.github.note} className="flex items-center gap-1 text-lamp">
                <TriangleAlert className="size-3" /> partial GitHub sync
              </span>
            ) : null}
          </span>
        ) : null}
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[240px_minmax(0,1fr)_420px]">
        <aside className="hidden min-h-0 border-r border-ink-700 bg-ink-900 md:block">
          <FileExplorer files={files} selected={file?.path ?? null} onSelect={(p) => openFile(p)} />
        </aside>
        <section className="hidden min-h-0 flex-col md:flex">
          <div className="flex shrink-0 items-center gap-1 border-b border-ink-700 bg-ink-900 px-2">
            <Tab active={view.kind === "code"} onClick={() => setView({ kind: "code" })} icon={Code2}>
              Code
            </Tab>
            <Tab
              active={view.kind === "history"}
              onClick={() => setView({ kind: "history" })}
              icon={History}
              disabled={!historyTarget}
            >
              History{historyTarget?.label ? ` · ${historyTarget.label}` : ""}
            </Tab>
            {view.kind === "commit" || view.kind === "record" ? (
              <Tab active onClick={() => {}} icon={GitCommitHorizontal}>
                {view.kind === "commit"
                  ? view.sha.slice(0, 10)
                  : `${view.evidence.source_type === "issue" ? "Issue" : "PR"} ${view.evidence.symbol_name}`}
              </Tab>
            ) : null}
          </div>
          <div className="min-h-0 flex-1">
            {view.kind === "code" ? (
              <CodeViewer
                file={file}
                highlight={highlight}
                focus={focus}
                onFocus={(f) => {
                  setHighlight(null);
                  setFocus(f);
                }}
              />
            ) : view.kind === "history" && historyTarget ? (
              <TimelinePanel
                repoId={repoId}
                target={historyTarget}
                onOpenCommit={(sha, path) => setView({ kind: "commit", sha, path })}
              />
            ) : view.kind === "commit" ? (
              <CommitView repoId={repoId} sha={view.sha} path={view.path} githubUrl={githubUrl} />
            ) : view.kind === "record" ? (
              <RecordView evidence={view.evidence} />
            ) : null}
          </div>
        </section>
        <aside className="min-h-0 border-l border-ink-700 bg-ink-900/60">
          <InvestigationPanel
            records={records}
            focus={focus}
            onClearFocus={() => setFocus(null)}
            onAsk={investigate}
            onStop={() => abort.current?.abort()}
            onOpenEvidence={openEvidence}
          />
        </aside>
      </div>
    </div>
  );
}

function Tab({
  active,
  onClick,
  icon: Icon,
  disabled,
  children,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ComponentType<{ className?: string }>;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`-mb-px flex max-w-72 items-center gap-1.5 border-b-2 px-3 py-2 text-xs transition disabled:opacity-40 ${
        active ? "border-lamp text-parchment" : "border-transparent text-muted hover:text-parchment"
      }`}
    >
      <Icon className={`size-3.5 shrink-0 ${active ? "text-lamp" : ""}`} />
      <span className="truncate">{children}</span>
    </button>
  );
}
