#!/usr/bin/env bash
# Runs the API (http://localhost:8000) and the frontend (http://localhost:3000) together.
# Ctrl+C stops both.
set -euo pipefail
cd "$(dirname "$0")"

trap 'kill 0' EXIT

(cd backend && uv run uvicorn app.main:app --port 8000 --reload --reload-dir app 2>&1 | sed 's/^/[api] /') &
(cd frontend && npm run dev 2>&1 | sed 's/^/[web] /') &

wait
