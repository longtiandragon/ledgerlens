#!/usr/bin/env bash
# Stop only the processes recorded by scripts/start-local.sh, matched by PID and start time.
# Usage: bash scripts/stop-local.sh
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pid_file="$root/runtime/processes-unix.txt"
if [[ ! -f "$pid_file" ]]; then
  echo "No services recorded by scripts/start-local.sh."
  exit 0
fi
while IFS=$'\t' read -r pid start; do
  [[ -n "$pid" ]] || continue
  if kill -0 "$pid" 2>/dev/null && [[ "$(ps -o lstart= -p "$pid" 2>/dev/null | sed 's/^ *//')" == "$start" ]]; then
    kill "$pid"
    echo "Stopped process $pid."
  fi
done < "$pid_file"
rm -f "$pid_file"
