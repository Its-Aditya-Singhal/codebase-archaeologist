# Codebase Archaeologist

An investigation tool that explains software repositories: what the code does, **why it exists,
where it came from, and how it evolved**. It answers from evidence retrieved from the repository
and cites every source, so the developer can check it.

> Status: **Phase 1** (ingestion + RAG) and **Phase 2** (Git / PR / issue intelligence) are
> working. See [docs/ROADMAP.md](docs/ROADMAP.md) for the architecture and the plan for phase 3
> (knowledge graph) and phase 4 (interactive investigation UI).

## What works today

- Index any public GitHub repo (`owner/repo` or URL), a private one with `GITHUB_TOKEN`, or a
  local git checkout
- Syntax-aware chunking (tree-sitter) so evidence is real functions, classes and doc sections
  with exact line ranges
- Hybrid retrieval: semantic vectors, identifier-aware keyword search, and symbol matching
- Select a symbol or line range, then ask. The selection is pinned as evidence, and the tool pulls in
  code that references it.
- History intelligence: commits, GitHub pull requests and issues, linked together. For any
  selected code, line history traces the commit that introduced it and later changes, each
  with its diff, PR and the issues that PR fixed. All of it is evidence for *why / who / when*.
- History tab: a timeline of every change to the selected code, plus commit and PR/issue views
- Streamed answers from Claude with inline `[S#]` citations; clicking one opens the source
  (code, commit, PR or issue)

## Stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | Next.js 16, React 19, TypeScript, Tailwind 4, Shiki | App Router workspace UI, fast syntax highlighting |
| API | Python 3.13, FastAPI | Ingestion/parsing/ML ecosystem (tree-sitter, local embeddings) lives in Python |
| Data | PostgreSQL 17 + pgvector (HNSW), `tsvector` | One store for relational data, vectors and full-text; graph edges fit here in phase 3 |
| Embeddings | `BAAI/bge-small-en-v1.5` via fastembed (local) | Indexing needs no API key; swappable behind `Embedder` |
| Answers | Claude Opus 5 via the Anthropic SDK | Long-context reasoning over code with strict citation discipline |

## Setup

Prerequisites: Node 20+, [uv](https://docs.astral.sh/uv/), git, and PostgreSQL 17 with pgvector.

```bash
# Postgres + pgvector (macOS/Homebrew), or: docker compose up -d
brew install postgresql@17 pgvector
brew services start postgresql@17
createdb archaeologist
```

```bash
# API: http://localhost:8000 (tables are created on startup)
cd backend
cp .env.example .env        # ANTHROPIC_API_KEY for answers; GITHUB_TOKEN strongly recommended
uv sync
uv run uvicorn app.main:app --port 8000 --reload --reload-dir app
```

If you use the Docker database, set
`DATABASE_URL=postgresql://archaeologist:archaeologist@localhost:5432/archaeologist` in `backend/.env`.

```bash
# Frontend: http://localhost:3000
cd frontend
npm install
npm run dev
```

The first indexing run downloads the embedding model (~130 MB). Without `GITHUB_TOKEN` the
GitHub API allows 60 requests/hour, so PR/issue sync for larger repos finishes over several
re-indexes (it resumes where it stopped). With a token it completes in one run.

## API

| Method | Path | |
|---|---|---|
| `POST` | `/api/repos` `{url}` | register + start indexing |
| `GET` | `/api/repos`, `/api/repos/{id}` | status, progress, stats |
| `POST` | `/api/repos/{id}/reindex` | re-clone/fetch and rebuild the index |
| `GET` | `/api/repos/{id}/files`, `/api/repos/{id}/file?path=` | explorer + file with symbols |
| `GET` | `/api/repos/{id}/history?path=&start_line=&end_line=` | commits that changed a range/file, with PRs/issues |
| `GET` | `/api/repos/{id}/commits/{sha}?path=` | commit details and diff |
| `POST` | `/api/repos/{id}/search` `{question, focus?}` | ranked evidence only (no LLM) |
| `POST` | `/api/repos/{id}/ask` `{question, focus?}` | SSE: `sources` → `delta`* → `done`/`error` |

`focus` is `{path, start_line?, end_line?}`.

## Development

```bash
cd backend && uv run pytest && uv run ruff check app tests
cd frontend && npx tsc --noEmit && npm run lint
```

## Layout

```
backend/app/
  ingestion/   repo_source (clone), filters, chunker (tree-sitter), pipeline
  history/     git_log (log, -L, blame-style range history), github (resumable PR/issue sync),
               links, ingest, provenance (code -> commits -> PRs -> issues), timeline
  retrieval/   hybrid (vector + lexical + symbol + focus/reference + provenance, RRF)
  answering/   prompts (evidence contract), answer (Claude streaming)
  embeddings/  Embedder protocol + local fastembed
  schema.sql   repositories · files · chunks · commits · pull_requests · issues · links
frontend/src/
  app/                  landing (sites) + /repos/[id] workspace
  components/workspace  FileExplorer · CodeViewer · Investigation
  components/history    TimelinePanel · CommitView/RecordView · Diff and PR/issue chips
  lib/api.ts            typed client + SSE reader
docs/ROADMAP.md         architecture and phase plan
```
