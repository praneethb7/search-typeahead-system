#!/usr/bin/env bash
#
# run.sh — one-command bootstrap + launch for the Search Typeahead System.
#
#   ./run.sh             start everything: 3 Redis cache nodes + the app
#   ./run.sh --dev       same, with auto-reload on code changes
#   ./run.sh --fresh     rebuild the venv and regenerate the dataset from scratch
#   ./run.sh --port 9000 serve on a custom port (default: 8000)
#   ./run.sh --no-cache  EMERGENCY ONLY: run without Redis (degrades to the trie;
#                        cache-hit-rate / latency marks depend on the cache, so
#                        do NOT use this for the graded demo)
#
# The distributed Redis cache is REQUIRED by default — cache hit rate and p95
# latency are part of the grade. The script starts the 3 cache nodes and waits
# until they are reachable before launching the app.
#
# Safe to re-run: anything already set up is skipped. Works regardless of the
# folder name (no hardcoded paths), so it runs on any machine with Python 3.10+.

set -euo pipefail

# --- always operate from the script's own directory --------------------------
cd "$(dirname "$0")"
ROOT="$(pwd)"

# --- defaults / arg parsing --------------------------------------------------
RELOAD=""
FRESH=0
PORT=8000
WITH_CACHE=1            # cache ON by default; --no-cache turns it off

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dev)       RELOAD="--reload"; shift ;;
    --fresh)     FRESH=1; shift ;;
    --no-cache)  WITH_CACHE=0; shift ;;
    --port)      PORT="${2:?--port needs a value}"; shift 2 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1 (try --help)"; exit 1 ;;
  esac
done

say()  { printf '\033[1;34m▸ %s\033[0m\n' "$*"; }
err()  { printf '\033[1;31m✖ %s\033[0m\n' "$*" >&2; }

# --- 1. find a Python interpreter (3.10+) ------------------------------------
PYBIN=""
for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PYBIN="$c"; break; fi
done
[[ -n "$PYBIN" ]] || { err "Python 3.10+ not found. Install it and retry."; exit 1; }

# --- 2. (re)create the virtualenv --------------------------------------------
VENV="$ROOT/.venv"
VPY="$VENV/bin/python"
# Rebuild if asked, missing, or its interpreter no longer runs (this is exactly
# what breaks when the project folder is renamed/copied).
if [[ "$FRESH" -eq 1 ]] || [[ ! -x "$VPY" ]] || ! "$VPY" -c 'import sys' >/dev/null 2>&1; then
  say "Creating virtual environment (.venv)…"
  rm -rf "$VENV"
  "$PYBIN" -m venv "$VENV"
fi

# --- 3. install dependencies (only when needed) ------------------------------
if [[ "$FRESH" -eq 1 ]] || ! "$VPY" -c 'import fastapi, uvicorn, redis, dotenv, httpx' >/dev/null 2>&1; then
  say "Installing dependencies…"
  "$VPY" -m pip install --quiet --upgrade pip
  "$VPY" -m pip install --quiet -r requirements.txt
fi

# --- 4. dataset + SQLite store -----------------------------------------------
if [[ "$FRESH" -eq 1 ]] || [[ ! -f "data/queries.csv" ]]; then
  say "Generating dataset (data/queries.csv)…"
  "$VPY" scripts/generate_dataset.py
fi
if [[ "$FRESH" -eq 1 ]] || [[ ! -f "data/typeahead.db" ]]; then
  say "Loading dataset into SQLite…"
  "$VPY" scripts/ingest.py
fi

# --- 5. distributed cache: 3 Redis nodes (REQUIRED) --------------------------
# Wait until a TCP port accepts connections (pure bash, no extra tools).
wait_for_port() {
  local host="$1" port="$2" tries="${3:-30}"
  for ((i=0; i<tries; i++)); do
    if (exec 3<>"/dev/tcp/${host}/${port}") 2>/dev/null; then exec 3>&- 3<&-; return 0; fi
    sleep 1
  done
  return 1
}

REDIS_PORTS=(6379 6380 6381)
if [[ "$WITH_CACHE" -eq 1 ]]; then
  if ! command -v docker >/dev/null 2>&1; then
    err "Docker is not installed, but the Redis cache is required for this demo."
    err "Install Docker Desktop (https://www.docker.com/products/docker-desktop),"
    err "start it, then re-run ./run.sh.  (Emergency-only fallback: ./run.sh --no-cache)"
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    err "Docker is installed but not running. Start Docker Desktop and re-run ./run.sh."
    err "(Emergency-only fallback: ./run.sh --no-cache)"
    exit 1
  fi

  # Clear any stale stack / leftover containers from a previous run (e.g. when
  # this project was run under a different folder name). Container names are
  # fixed, so an old set can keep the ports 6379/6380/6381 bound.
  docker compose down --remove-orphans >/dev/null 2>&1 || true
  docker rm -f typeahead-redis-c0 typeahead-redis-c1 typeahead-redis-c2 >/dev/null 2>&1 || true

  # If a *non-Docker* process still holds any cache port, point it out clearly.
  for p in "${REDIS_PORTS[@]}"; do
    if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then
      err "Port $p is already in use by another process:"
      lsof -nP -iTCP:"$p" -sTCP:LISTEN | sed 's/^/    /' >&2
      err "Stop that process (e.g. a local 'redis-server' or 'brew services stop redis'),"
      err "or free port $p, then re-run ./run.sh."
      exit 1
    fi
  done

  say "Starting 3 Redis cache nodes (docker compose)…"
  docker compose up -d

  say "Waiting for cache nodes to accept connections…"
  for p in "${REDIS_PORTS[@]}"; do
    if wait_for_port localhost "$p" 30; then
      echo "    • redis node on :$p ready"
    else
      err "Redis node on port $p did not come up in time."
      err "Check 'docker compose ps' and 'docker compose logs'."
      exit 1
    fi
  done
  say "Distributed cache is up (consistent-hash ring across :6379 :6380 :6381)."
else
  err "Running WITHOUT the Redis cache (--no-cache)."
  err "Cache-hit-rate and latency results will NOT reflect the cached design —"
  err "do not use this mode for the graded demo."
fi

# --- 6. launch ----------------------------------------------------------------
say "Starting server → http://localhost:${PORT}/   (API docs: /docs)"
echo "  Press Ctrl+C to stop the app.  Redis keeps running; stop it with: docker compose down"
exec "$VPY" -m uvicorn backend.main:app --host 0.0.0.0 --port "$PORT" $RELOAD
