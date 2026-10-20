#!/usr/bin/env bash
# Start the local API (port 8000) and web dev server (port 5188) on Linux or macOS.
# Mirrors scripts/start-local.ps1: refuses busy ports, never stops a process it did
# not start, prepares (and if needed backs up and migrates) the local SQLite database
# first, and records only its own processes with their start times.
# Usage: bash scripts/start-local.sh [--skip-install]
set -euo pipefail

skip_install=0
if [[ "${1:-}" == "--skip-install" ]]; then skip_install=1; fi
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
export UV_CACHE_DIR="$root/.cache/uv" npm_config_cache="$root/.cache/npm"
export PYTHONPATH="$root/backend:$root" PYTHONUNBUFFERED=1
runtime="$root/runtime"
mkdir -p "$runtime"
pid_file="$runtime/processes-unix.txt"

started_at() { ps -o lstart= -p "$1" 2>/dev/null | sed 's/^ *//'; }

if [[ -f "$pid_file" ]]; then
  while IFS=$'\t' read -r pid start; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null && [[ "$(started_at "$pid")" == "$start" ]]; then
      echo "Recorded services still exist. Run scripts/stop-local.sh first." >&2
      exit 1
    fi
  done < "$pid_file"
fi

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
for port in 8000 5188; do
  if port_busy "$port"; then
    echo "Port $port is already in use. No existing process was stopped." >&2
    exit 1
  fi
done

if [[ $skip_install -eq 0 ]]; then
  uv sync --frozen
  (cd frontend && npm ci)
fi

python_bin="$root/.venv/bin/python"
if ! "$python_bin" scripts/prepare_local_database.py; then
  echo "Local database preparation failed. No API or frontend process was started." >&2
  exit 1
fi

owned=()
stop_owned() {
  for pid in "${owned[@]}"; do kill "$pid" 2>/dev/null || true; done
}
wait_ready() {
  for _ in $(seq 1 60); do
    if curl -fsS -o /dev/null --max-time 2 "$1"; then return 0; fi
    sleep 0.5
  done
  echo "Service did not become ready: $1. See runtime logs." >&2
  return 1
}

nohup "$python_bin" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 \
  > "$runtime/api.log" 2> "$runtime/api-error.log" &
owned+=("$!")
if ! wait_ready http://127.0.0.1:8000/api/v1/health; then stop_owned; exit 1; fi

cd "$root/frontend"
nohup node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5188 --strictPort \
  > "$runtime/web.log" 2> "$runtime/web-error.log" &
owned+=("$!")
cd "$root"
if ! wait_ready http://127.0.0.1:5188; then stop_owned; exit 1; fi

: > "$pid_file"
for pid in "${owned[@]}"; do printf '%s\t%s\n' "$pid" "$(started_at "$pid")" >> "$pid_file"; done
echo "LedgerLens ready: http://127.0.0.1:5188  API: http://127.0.0.1:8000/docs"
