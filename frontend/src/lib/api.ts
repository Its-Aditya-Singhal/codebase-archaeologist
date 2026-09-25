export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type RepoStatus =
  | "queued"
  | "cloning"
  | "parsing"
  | "embedding"
  | "history"
  | "ready"
  | "failed";

export interface Repo {
  id: number;
  url: string;
  owner: string | null;
  name: string;
  default_branch: string | null;
  head_sha: string | null;
  status: RepoStatus;
  progress: { step?: string; done?: number; total?: number };
  stats: {
    files?: number;
    chunks?: number;
    symbols?: number;
    skipped_files?: number;
    languages?: Record<string, number>;
    history?: {
      commits?: number;
      pull_requests?: number;
      issues?: number;
      links?: number;
      error?: string;
      github?: { complete: boolean; note: string | null };
    };
  };
  error: string | null;
  created_at: string;
  indexed_at: string | null;
}

export interface RepoFile {
  path: string;
  language: string | null;
  line_count: number;
  symbols: number;
}

export interface SymbolInfo {
  id: number;
  kind: string;
  name: string;
  start_line: number;
  end_line: number;
}

export interface FileDetail {
  path: string;
  language: string | null;
  line_count: number;
  content: string;
  symbols: SymbolInfo[];
}

export interface Focus {
  path: string;
  start_line?: number;
  end_line?: number;
  label?: string; // UI only
}

export interface Evidence {
  ref: string; // S1, S2, ...
  id: number;
  source_type: string;
  path: string | null;
  language: string | null;
  symbol_kind: string | null;
  symbol_name: string | null;
  start_line: number | null;
  end_line: number | null;
  content: string;
  score: number;
  matched_by: string[];
  metadata: Record<string, string | number | null>;
}

export interface PullRequestRef {
  number: number;
  title: string | null;
  state: string | null;
  url: string | null;
  author?: string | null;
  merged_at?: string | null;
}

export interface IssueRef {
  number: number;
  title: string;
  state: string;
  url: string;
  kind: string;
}

export interface TimelineCommit {
  sha: string;
  author: string;
  email: string;
  date: string;
  subject: string;
  diff: string | null;
  role?: "introduced";
  pull_requests: PullRequestRef[];
  issues: IssueRef[];
}

export interface Timeline {
  scope: "range" | "file";
  path: string;
  start_line: number | null;
  end_line: number | null;
  commits: TimelineCommit[];
  authors: string[];
}

export interface CommitDetail {
  sha: string;
  parent_shas: string[];
  author_name: string;
  author_email: string;
  authored_at: string;
  subject: string;
  body: string;
  files_changed: number;
  insertions: number;
  deletions: number;
  files: { path: string; insertions: number | null; deletions: number | null }[];
  diff: string;
  diff_path: string | null;
  pull_requests: PullRequestRef[];
  issues: IssueRef[];
}

export const IN_PROGRESS: RepoStatus[] = ["queued", "cloning", "parsing", "embedding", "history"];

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {}
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listRepos: () => fetch(`${API_URL}/api/repos`).then(json<Repo[]>),
  getRepo: (id: number) => fetch(`${API_URL}/api/repos/${id}`).then(json<Repo>),
  createRepo: (url: string) =>
    fetch(`${API_URL}/api/repos`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ url }),
    }).then(json<Repo>),
  reindex: (id: number) =>
    fetch(`${API_URL}/api/repos/${id}/reindex`, { method: "POST" }).then(json<Repo>),
  deleteRepo: (id: number) => fetch(`${API_URL}/api/repos/${id}`, { method: "DELETE" }),
  listFiles: (id: number) => fetch(`${API_URL}/api/repos/${id}/files`).then(json<RepoFile[]>),
  getFile: (id: number, path: string) =>
    fetch(`${API_URL}/api/repos/${id}/file?path=${encodeURIComponent(path)}`).then(
      json<FileDetail>,
    ),
  history: (id: number, focus: Focus) => {
    const q = new URLSearchParams({ path: focus.path });
    if (focus.start_line) q.set("start_line", String(focus.start_line));
    if (focus.end_line) q.set("end_line", String(focus.end_line));
    return fetch(`${API_URL}/api/repos/${id}/history?${q}`).then(json<Timeline>);
  },
  commit: (id: number, sha: string, path?: string | null) =>
    fetch(
      `${API_URL}/api/repos/${id}/commits/${sha}${path ? `?path=${encodeURIComponent(path)}` : ""}`,
    ).then(json<CommitDetail>),
};

export type AskEvent =
  | { event: "sources"; data: Evidence[] }
  | { event: "delta"; data: { text: string } }
  | { event: "done"; data: { stop_reason: string; model: string; usage: Record<string, number> } }
  | { event: "error"; data: { message: string } };

/** POST /ask and yield server-sent events as they arrive. */
export async function* ask(
  repoId: number,
  question: string,
  focus: Focus | null,
  signal?: AbortSignal,
): AsyncGenerator<AskEvent> {
  const res = await fetch(`${API_URL}/api/repos/${repoId}/ask`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      question,
      focus: focus
        ? { path: focus.path, start_line: focus.start_line, end_line: focus.end_line }
        : null,
    }),
    signal,
  });
  if (!res.ok || !res.body) {
    yield { event: "error", data: { message: (await res.text()) || res.statusText } };
    return;
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of raw.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length) yield { event, data: JSON.parse(data.join("\n")) } as AskEvent;
    }
  }
}
