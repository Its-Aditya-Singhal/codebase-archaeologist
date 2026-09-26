# Codebase Archaeologist

An investigation tool that explains software repositories: what the code does, **why it exists,
where it came from, and how it evolved**. It answers from evidence retrieved from the repository
and cites every source, so the developer can check it.

> Status: **Phases 1–3** (ingestion + RAG, Git / PR / issue intelligence, knowledge graph) are
> complete, and the **backend of phase 4** (explorer, evolution, case files, agent mode) is
> built and tested; its UI is next. See [docs/ROADMAP.md](docs/ROADMAP.md).

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
- Knowledge graph: files, functions, classes, packages, commits, PRs, issues and authors in one
  graph, with calls, imports, inheritance and declared dependencies extracted from Python,
  JS/TS, Go, Rust, Java, Kotlin, C#, PHP, Ruby and C/C++
- Relations tab: who calls the selected code, what it calls, what it inherits and which
  packages it uses (two hops), with the issue → PR → commit chain behind it. Click any node
  to move the investigation there.
- Impact analysis: everything that reaches the code (3 hops), the tests that exercise it,
  files that historically change with it, and a change-risk verdict with its reasons. "What
  would break if…" questions get this as cited evidence.
- Follow-up questions and saved investigations (case files) with their evidence
- Agent mode: a model gathers evidence over several tool-using steps before answering
- Evolution: a function's code at every version, with the PR behind each change
- PR and issue discussions (incl. review comments) as evidence
- Incremental re-indexing: only new or changed code and history is re-embedded
- Streamed, cited answers with inline `[S#]` citations; clicking one opens the source
  (code, commit, PR or issue). Four answer writers, picked automatically:
  **Gemini** (free Google AI Studio key), an **evidence briefing** (no model, free),
  a **local model via Ollama** (free), or **Claude** (paid API key)
- Accounts: email/password sign-up and login; each user sees only their own repositories and
  case files (a repository added by several users is indexed once and shared)

## Stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | Next.js 16, React 19, TypeScript, Tailwind 4, Shiki, Motion | App Router workspace UI, fast syntax highlighting |
| API | Python 3.13, FastAPI | Ingestion/parsing/ML ecosystem (tree-sitter, local embeddings) lives in Python |
| Data | PostgreSQL 17 + pgvector (HNSW), `tsvector` | One store for relational data, vectors, full-text and the graph (node/edge tables) |
| Embeddings | `BAAI/bge-small-en-v1.5` via fastembed (local) | Indexing needs no API key; swappable behind `Embedder` |
| Answers | Gemini (free tier) · evidence briefing (no model) · Ollama local model · Claude | Works for free; writers are drop-in |
| Auth | scrypt password hashes, HttpOnly session cookie (hashed in the DB) | Standard library only, no auth service |

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
cp .env.example .env        # everything is optional; see "Answers" below
uv sync
uv run uvicorn app.main:app --port 8000 --reload --reload-dir app
```

After the first setup, `./dev.sh` from the repo root starts the API and the frontend together
(Ctrl+C stops both).

If you use the Docker database, set
`DATABASE_URL=postgresql://archaeologist:archaeologist@localhost:5432/archaeologist` in `backend/.env`.

```bash
# Frontend: http://localhost:3000
cd frontend
npm install
npm run dev
```

### Answers (all free options work without an account)

| Writer | Setup | What you get |
|---|---|---|
| Gemini | `GEMINI_API_KEY` in `backend/.env` ([free key](https://aistudio.google.com/apikey)) | Written, cited answers and agent mode. Free tier: rate limited per minute/day, and Google may use free-tier prompts (your indexed code) to improve its products |
| Evidence briefing | nothing | A cited digest of the evidence: what the code is, where it came from, what calls it, impact. No interpretation. |
| Local model (Ollama) | `brew install ollama`, `ollama serve`, `ollama pull qwen2.5-coder:7b` (~4.7 GB; 16 GB RAM recommended) | Written, cited answers, generated on your machine |
| Claude | `ANTHROPIC_API_KEY` in `backend/.env` (paid) | The strongest reasoning and citation discipline |

`ANSWER_PROVIDER=auto` (default) uses Gemini if its key is set, else Claude if its key is set,
else Ollama if it is running with the model pulled, else the briefing. `GEMINI_MODEL` (default
`gemini-3.8-flash`) is retried on `GEMINI_FALLBACK_MODEL` (`gemini-3.5-flash-lite`, its own
quota) when rate limited. `OLLAMA_MODEL` picks another local model.

### Accounts

Open the app and create an account. The **first** account adopts the repositories and
investigations created before accounts existed. Set `ALLOW_SIGNUP=false` to stop new sign-ups,
and `COOKIE_SECURE=true` when serving over HTTPS.

The first indexing run downloads the embedding model (~130 MB). Without `GITHUB_TOKEN` the
GitHub API allows 60 requests/hour, so PR/issue sync for larger repos finishes over several
re-indexes (it resumes where it stopped). With a token it completes in one run.

## API

| Method | Path | |
|---|---|---|
| `POST` | `/api/auth/signup` `{email, password, name}` · `/api/auth/login` · `/api/auth/logout` | sets / clears the session cookie |
| `GET` | `/api/auth/me` · `/api/auth/config` | current user · whether sign-up is open |
| `POST` | `/api/repos` `{url}` | register + start indexing |
| `GET` | `/api/repos`, `/api/repos/{id}` | status, progress, stats |
| `POST` | `/api/repos/{id}/reindex` | re-clone/fetch and update the index (incremental) |
| `GET` | `/api/repos/{id}/files`, `/api/repos/{id}/file?path=` | explorer + file with symbols |
| `GET` | `/api/repos/{id}/history?path=&start_line=&end_line=` | commits that changed a range/file, with PRs/issues |
| `GET` | `/api/repos/{id}/commits/{sha}?path=` | commit details and diff |
| `GET` | `/api/repos/{id}/graph?path=&start_line=&end_line=` | graph neighbourhood of a symbol/file + provenance chain |
| `GET` | `/api/repos/{id}/impact?path=&start_line=&end_line=` | dependents, tests, co-change, risk |
| `POST` | `/api/repos/{id}/graph/rebuild` | rebuild only the graph from the stored index |
| `GET` | `/api/repos/{id}/graph/overview?level=file\|dir&depth=` | architecture map: aggregated dependencies, churn, packages |
| `GET` | `/api/repos/{id}/graph/search?q=&kinds=` | find graph nodes by name |
| `GET` | `/api/repos/{id}/graph/nodes/{node}` · `/expand?kinds=&direction=&since=&until=` | node detail · neighbours, time-filtered |
| `GET` | `/api/repos/{id}/evolution?path=&start_line=&end_line=` | a definition's versions over time with diffs and PRs |
| `POST` | `/api/repos/{id}/search` `{question, focus?}` | ranked evidence only (no LLM) |
| `POST` | `/api/repos/{id}/ask` `{question, focus?, investigation_id?, mode?}` | SSE: `investigation` → `step`* (agent) → `sources` → `delta`* → `done`/`error` |
| `GET` | `/api/repos/{id}/investigations` | saved investigations |
| `GET` `PATCH` `DELETE` | `/api/investigations/{inv}` | open (turns with evidence) · rename · delete |

`focus` is `{path, start_line?, end_line?}`; `mode` is `answer` (default) or `agent`.
Everything except `/api/health` and `/api/auth/*` needs a session; another user's repositories
and investigations answer 404.

## Development

```bash
cd backend && uv run pytest && uv run ruff check app tests
cd frontend && npx tsc --noEmit && npm run lint
```

`pytest` runs unit tests plus integration tests that index a generated git repository through
the API into a throwaway `archaeologist_test` database (skipped if PostgreSQL isn't running).
Schema changes go in a new numbered file in `backend/app/migrations/`; they apply on startup.

## Layout

```
backend/app/
  ingestion/   repo_source (clone), filters, chunker (tree-sitter), pipeline
  history/     git_log (log, -L, blame-style range history), github (resumable PR/issue sync),
               links, ingest (+ discussions), provenance (code -> commits -> PRs -> issues),
               timeline, evolution (a definition's versions)
  graph/       extract (tree-sitter imports/calls/bases), resolve (module systems), manifests,
               build (graph over code + history), query (neighbourhood, impact, related code),
               explore (overview, search, expand)
  retrieval/   hybrid (vector + lexical + symbol + focus + graph + provenance, RRF)
  answering/   prompts, answer (Gemini | Claude | Ollama | briefing), gemini, briefing,
               agent (tool loop)
  auth.py      sign-up, login, sessions, per-user access guard
  investigations.py  case files: saved turns, follow-up context
  migrations/  numbered SQL, applied in order on startup
  embeddings/  Embedder protocol + local fastembed
frontend/src/
  app/                  / landing · /app sites · /repos/[id] workspace · /login, /signup
  components/landing    Hero (live demo) · Strata · EvidenceChain · Sections (scroll-driven, motion)
  components/motion     Reveal · Stagger · CountUp · GlowCard (respect prefers-reduced-motion)
  components/auth       AuthProvider (session gate) · AuthForm · AuthArt · UserMenu
  components/workspace  FileExplorer · CodeViewer · Investigation
  components/history    TimelinePanel · CommitView/RecordView · Diff and PR/issue chips
  components/graph      RelationsPanel · NeighborhoodGraph · ImpactView
  lib/api.ts            typed client + SSE reader
docs/ROADMAP.md         architecture and phase plan
```
