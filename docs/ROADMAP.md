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
                                │    └── (phase 3) graph/                                            │
                                └───────────────────────────────┬────────────────────────────────────┘
                                                                │
                                                   PostgreSQL 17 + pgvector
                     repositories · files · chunks (vector + tsvector)
                     commits · commit_files · pull_requests · issues · links
```

Module boundaries (each can evolve independently):

| Module | Owns | Depends on |
|---|---|---|
| `ingestion` | turning a repo into `files` + `chunks` | embeddings, db |
| `retrieval` | ranking evidence for a question (+ optional focus) | embeddings, db, history |
| `history` | commits, PRs, issues, links; line provenance | db, GitHub API, git |
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

## Phase 3: knowledge graph / relationship layer

Goal: *what is related* and *what breaks if I change this*.

1. `entities` (repo, file, symbol, commit, PR, issue, author, external dependency) and typed
   `edges` (`defines`, `calls`, `imports`, `modifies`, `introduced_by`, `fixes`, `authored`,
   `depends_on`) in Postgres. Recursive CTEs are enough to start; move to a graph DB only if
   traversal performance demands it.
2. Extraction: tree-sitter import/call extraction (per-language queries), manifest parsing
   (`package.json`, `pyproject.toml`, `go.mod`, …) for dependencies, phase-2 links for history.
3. Graph-augmented retrieval: expand from focus/top hits along edges (1–2 hops) and fuse with
   the text retrievers; replaces phase 1's lexical "reference" heuristic.
4. Impact analysis endpoint: reverse `calls`/`imports` closure plus co-change frequency from history.

## Phase 4: investigation experience

- Interactive relationship graph (issue → PR → commit → file → function → dependency) with
  pan/zoom, expand-on-click, and filtering by type and time
- Evolution view: a function's versions over time with diffs and the PRs that changed it
- "Case file" investigations: saved, shareable question threads with their evidence
- Agentic investigations: multi-step tool use (search, blame, open PR, traverse graph) for
  questions that need several hops, with the evidence trail shown as it is gathered

## Known limitations / next improvements

- Embedding runs on CPU (~20 chunks/s). Fine for small/medium repos; a hosted code-embedding
  model or GPU would be the upgrade path (swap via `Embedder`).
- Re-index rebuilds everything; incremental re-index by `blob_sha` is straightforward to add.
- Single-turn questions (no follow-up context yet).
- Ingestion runs in-process on a thread pool; move to a job queue once there are many repos.
- Without `GITHUB_TOKEN`, large repos need several re-indexes (an hour apart) to fetch all
  PRs and issues; the UI flags a partial sync.
