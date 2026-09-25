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
                                │    └── (phase 2) history/   (phase 3) graph/                        │
                                └───────────────────────────────┬────────────────────────────────────┘
                                                                │
                                                   PostgreSQL 17 + pgvector
                                     repositories · files · chunks (vector + tsvector)
```

Module boundaries (each can evolve independently):

| Module | Owns | Depends on |
|---|---|---|
| `ingestion` | turning a repo into `files` + `chunks` | embeddings, db |
| `retrieval` | ranking evidence for a question (+ optional focus) | embeddings, db |
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

## Phase 2: Git / PR / issue intelligence

Goal: answer *why*, *who*, *when*, and *how it evolved*.

1. **Commit ingestion** from the existing clone: `git log --numstat` → `commits` table
   (sha, author, date, message, files touched) + commit-message chunks (`source_type=commit`).
2. **Line provenance**: `git blame --porcelain` on demand for a focused range → the commits
   that last touched those lines; `git log -L` for a function's evolution. Cache per (file, blob_sha).
3. **GitHub API** (token optional for public repos): PRs (title, body, review comments, merged
   commits) and issues (body, comments). Link PR ↔ commits by merge/squash SHAs, and PR ↔
   issue via "fixes #123" references and the timeline API. Store as chunks + link table.
4. **History-aware retrieval**: for a focused range, blame → commits → PRs → linked issues are
   pinned as evidence, ahead of the semantic hits. This is the core of "why does this use Redis?".
5. Prompt: add `commit`, `pull_request`, `issue` to `indexed_sources`; answers cite commit
   SHAs, PR/issue numbers with authors and dates.
6. UI: a history timeline for the focused code (commits and PRs on a time axis), blame gutter.

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

## Known limitations / next improvements in phase 1

- Embedding runs on CPU (~20 chunks/s). Fine for small/medium repos; a hosted code-embedding
  model or GPU would be the upgrade path (swap via `Embedder`).
- Re-index rebuilds everything; incremental re-index by `blob_sha` is straightforward to add.
- Single-turn questions (no follow-up context yet).
- Ingestion runs in-process on a thread pool; move to a job queue once there are many repos.
