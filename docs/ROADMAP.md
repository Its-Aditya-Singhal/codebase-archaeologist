# Architecture & Roadmap

## Guiding principle

Answers must be built from evidence in the repository, and every claim must point back to a
source the developer can open. The system is built so that the retriever gets richer from phase to phase
(code → history → graph) while the answering contract stays the same: numbered evidence in,
cited answer out.

## Architecture

```
┌──────────────┐   REST + SSE   ┌──────────────────────── backend (FastAPI) ────────────────────────┐
│  frontend    │ ─────────────▶ │  api (app/main.py)                                                 │
│  Next.js     │                │    │                                                               │
│  workspace   │                │    ├── ingestion/   clone → filter → chunk (tree-sitter) → embed   │
└──────────────┘                │    ├── retrieval/   hybrid: pgvector + tsvector + symbols + focus  │
                                │    ├── answering/   evidence → Claude → streamed, cited answer     │
                                │    ├── embeddings/  Embedder protocol (local fastembed default)    │
                                │    ├── history/     git log/-L, GitHub PRs+issues, links, provenance│
                                │    └── graph/       extract → resolve → build; neighbourhood, impact│
                                └───────────────────────────────┬────────────────────────────────────┘
                                                                │
                                                   PostgreSQL 17 + pgvector
                     repositories · files · chunks (vector + tsvector)
                     commits · commit_files · pull_requests · issues · links
                     graph_nodes · graph_edges
```

Module boundaries (each can evolve independently):

| Module | Owns | Depends on |
|---|---|---|
| `ingestion` | turning a repo into `files` + `chunks` | embeddings, db |
| `retrieval` | ranking evidence for a question (+ optional focus) | embeddings, db, history, graph |
| `history` | commits, PRs, issues, links; line provenance | db, GitHub API, git |
| `graph` | entities + typed edges over code and history; traversal, impact | db, history (provenance) |
| `answering` | prompt contract, Claude call, streaming | retrieval output only |
| `embeddings` | the `Embedder` protocol | — |
| frontend | investigation UX | HTTP API only |

`chunks.source_type` is the extension point: commits, PRs and issues become new source types that
flow through the same retrieval and answering path.

## Phase 1: repository ingestion + RAG ✅

- Clone any GitHub repo (blobless clone keeps the full history for phase 2) or a local checkout
- Tree-sitter chunking by function/class/method for 15+ languages; markdown by heading;
  line windows as fallback. Each chunk carries a symbol name + exact line range.
- Local embeddings (`BAAI/bge-small-en-v1.5`, 384d), so indexing works with no API key
- Hybrid retrieval with Reciprocal Rank Fusion: dense vectors, lexical `tsvector` over
  identifier-expanded text (`getUserById` → get user by id), exact symbol-name matches
- Focus-aware retrieval: the selected symbol is pinned; its code enriches the query; lexical
  "reference" search finds call sites, tests and docs that mention it
- Grounded answering (Claude Opus 5, adaptive thinking): strict citation contract `[S#]`,
  explicit "the indexed sources don't show this" instead of guessing
- Workspace UI: file explorer, highlighted code viewer, symbol/line-range focus, streamed
  answers with clickable citations that open and highlight the source

## Phase 2: Git / PR / issue intelligence ✅

Goal: answer *why*, *who*, *when*, and *how it evolved*.

- **Full clones** (line history needs every historical blob; a blobless clone made
  `git log -L` take 40 s+ instead of 40 ms)
- **Commit ingestion**: `git log --numstat` → `commits` + `commit_files`; every non-merge
  commit becomes a `commit` chunk (message + files touched) for semantic and keyword search
- **GitHub PRs and issues** via REST, resumable under rate limits: newest-updated first,
  stop at the last complete crawl's watermark, and when cut short store GitHub's `next` link
  (page- or cursor-based) in `repositories.sync`, with retry on transient network errors.
  Upserted, never bulk-deleted, so data builds up across runs.
- **Links** (`links` table, seed of the phase 3 graph): commit → PR from squash `(#N)` and
  `Merge pull request #N` subjects plus `merge_commit_sha`; commits inside merged branches
  (`rev-list p2 ^p1`) → PR; PR/commit/issue → issue via GitHub closing keywords
  (`fixes`) and `#N` mentions
- **Line provenance**: `git log -L` on the focused range (or the symbol a question names)
  → the introducing commit + the most recent changes, each with the diff of that range →
  their PRs → the issues those PRs fix. These are pinned as evidence right after the code.
- **One-hop expansion**: a commit found by search brings its PR; a PR brings the issues it fixes
- **Prompt**: history-aware rules (roles `introduced`/`modified`, cite SHAs/PRs/issues,
  caveat that line history doesn't cross files)
- **UI**: History tab with a line or file timeline (origin marker, PR/issue chips, inline
  range diffs), commit view (message, stats, scoped/full diff), PR/issue view, and
  type-aware evidence rows
- **API**: `GET /history?path&start_line&end_line`, `GET /commits/{sha}?path`

Deferred to later phases: PR review comments and issue comment threads (one request per
item, costly without a token), cross-file code movement (`git log -L` stays within a file;
phase 3's symbol graph can follow moves), incremental re-indexing.

## Phase 3: knowledge graph / relationship layer ✅

Goal: *what is related* and *what breaks if I change this*.

- **One graph over code and history** (`graph_nodes` / `graph_edges`). Nodes: file, symbol,
  module (external import), dependency (declared in a manifest), commit, pull request, issue,
  author. Edges: `defines`, `contains`, `calls`, `inherits`, `imports`, `declares`, `modifies`,
  `authored`, `merged_in`, `part_of`, `fixes`, `mentions`, so the chain
  issue → PR → commit → file → function → dependency is a path in one table.
- **Extraction** (`graph/extract.py`): one tree-sitter walk per file, driven by per-language
  tables, yields imports, call sites (name + receiver) and base classes for Python, JS/TS/TSX
  (incl. JSX component usage, `require`), Go, Rust, Java, Kotlin, C#, PHP, Ruby, C/C++.
  Symbols are *definitions* (`chunker.definitions`), independent of retrieval chunking, so
  methods of small classes are nodes too; each points at the chunk that holds it.
- **Resolution** (`graph/resolve.py`, `graph/build.py`): imports → repository files by each
  language's module rules (Python packages + relative imports + source roots, TS/JS relative
  and `@/` aliases + index files, Go modules from `go.mod`, Java/Kotlin/PHP paths, Rust
  `crate::`, Ruby `require_relative`, C includes), else an external package, matched to
  manifest dependencies (`package.json`, `pyproject.toml`, `requirements*.txt`, `setup.py`,
  `go.mod`, `Cargo.toml`, `Gemfile`). Calls resolve by scope: explicit import (following
  package re-exports) → `self`/`this` within the class → inherited methods → same file /
  class / package → module alias (`utils.f()`) → class-qualified (`Queue.create()`) →
  receiver named after a class (`queue.enqueue()` → `Queue.enqueue`) → a unique definition.
  Each edge records `via` and a confidence (1.0 for explicit scope, 0.4 for a name-only match).
  On rq: 5,866 call sites to repository names, 79% resolved; the build takes under a second.
- **Graph-augmented retrieval**: the code under investigation (focus, or the symbol the
  question names) pulls in its callers, callees, bases and subclasses, production code first,
  each labelled with its relation (`relation="calls Queue.enqueue_call"`) for the model. The
  lexical "reference" search remains as the fallback when the graph has nothing.
- **Impact analysis** (`GET /impact`): reverse closure over calls/inheritance (and imports for
  files) to 3 hops with path confidence, tests reached, co-change from history (files that
  change in the same commits, ignoring sweeping commits), churn, and a transparent risk
  heuristic where every point carries its reason. "What would break" questions get it as a
  `type="graph"` evidence source.
- **UI**: a Relations tab with a layered neighbourhood graph (callers two hops ← target →
  callees, bases, packages used; the provenance lane issue → PR → commit underneath; click to
  re-centre, ↗ to open code, dashed = inferred) and an Impact view (risk, blast-radius rings,
  dependents, tests, co-change). Caller counts appear in the code viewer's symbol bar.

Known gaps: name-based resolution cannot see dynamic dispatch, callbacks passed as values,
reflection or string-based lookups, and a variable receiver not named after its type
(`q.enqueue()`) resolves only when the method name is unique. Type inference (e.g. via a
language server or stack-graphs) would raise precision. Symbol-level `modifies` edges are
computed on demand with `git log -L` rather than stored.

## Phase 4: investigation experience ✅

Every phase-4 feature has its backend and its workspace UI.

- **Relationship explorer** (`graph/explore.py`): `GET /graph/overview` (architecture at file
  or directory level: dependency edges aggregated from imports, calls and inheritance, node
  size/churn/test share, external packages), `/graph/search`, `/graph/nodes/{id}` (degree per
  edge kind) and `/graph/nodes/{id}/expand` (neighbours by edge kind and direction, with
  commits/PRs/issues filtered to a date window). **UI**: the Map tab, a pan/zoom
  canvas (force layout, no graph library) with the architecture at directory or file level,
  and an explorer that expands nodes on double-click with relation-type, direction and date
  filters.
- **Evolution** (`history/evolution.py`, `GET /evolution`): a definition's versions, oldest
  first. The `git log -L` hunk header gives the range's position at each commit; slicing the
  file at that commit reconstructs the code as it was, alongside the diff and the PR/issues.
  A selection inside a function widens to the function. **UI**: the Evolution tab, a version
  stepper (← →) showing the code at each version with changed lines marked, the diff, and
  the PR/issues.
- **Case files and follow-ups** (`investigations.py`): every `/ask` turn is stored with focus,
  evidence, answer and writer; `investigation_id` continues a thread. A short or referential
  follow-up ("who calls it?") borrows the previous question's subject for retrieval, and the
  writer receives the earlier turns (citations stripped, since they referred to old evidence).
  Endpoints to list, open, rename and delete. **UI**: a case-files drawer
  (reopen, rename, delete), follow-up threads and a New investigation button.
- **Agentic investigations** (`answering/agent.py`, `/ask` with `mode: "agent"`): the model
  first gathers evidence with seven tools (search, read_code, find_symbol, code_history,
  relations, impact, open_record) for up to 6 rounds; each tool result joins one numbered
  evidence pool and is streamed as a `step` event; then the normal writer answers from the
  pool with the same citation contract. Works with Claude or a local Ollama model; without a
  model it falls back to single-pass retrieval and says so. Arguments are validated and
  repeated calls refused. **UI**: an Answer/Agent switch and
  a live evidence trail of each tool call and the sources it added.

Also completed alongside:

- **Discussions**: PR conversation and review comments (with the file they were left on)
  and issue comments, fetched within a request budget, threads behind code first, resumable,
  and re-fetched only when the PR/issue changed. They become part of the PR/issue evidence.
  PR comment counts come free from the issues listing, so silent PRs cost no requests.
- **Incremental re-indexing**: chunks are matched by the hash of their embedding input; only
  new or changed text is embedded (rq: 7.5 min full → 12 s re-index with nothing changed).
  Changing the chunker (`INDEX_VERSION`) or embedding model forces a full rebuild.
- **Durable indexing**: indexing interrupted by a restart resumes on startup (cheap, since it
  is incremental); a job interrupted twice is marked failed instead of looping.
- **Migrations**: versioned SQL in `app/migrations`, applied in order under an advisory lock
  and recorded in `schema_migrations`.
- **Free answering**: evidence briefing (no model) and local Ollama models besides Claude.
- **Integration tests**: the API end to end against a throwaway `archaeologist_test`
  database and a generated git repository.

## Known limitations / next improvements

- Embedding runs on CPU (~20 chunks/s). Fine for small/medium repos; a hosted code-embedding
  model or GPU would be the upgrade path (swap via `Embedder`).
- Ingestion runs in-process on a thread pool (now resumable); a separate worker process or
  job queue is the step up once many repositories are indexed concurrently.
- Without `GITHUB_TOKEN` (60 requests/hour), large repos need several re-indexes an hour apart
  to fetch all PRs, issues and discussions; everything resumes where it stopped.
- The Gemini answer, follow-up and agent paths have been run against the live free-tier API
  (Sept 2026); free-tier models are often overloaded, so overloads (5xx) and rate limits (429)
  fall back to the lighter model. The Claude path is tested only against canned API responses.
- Accounts are email/password only: no email verification, password reset or OAuth yet. Login
  throttling is in memory (per process). A server-wide `GITHUB_TOKEN` is used for every user's
  clones, so on a shared deployment it should not have access to private repositories.
