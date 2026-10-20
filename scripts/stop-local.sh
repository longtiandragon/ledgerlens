#!/usr/bin/env bash
# Stop only the processes recorded by scripts/start-local.sh, matched by PID and start time,
# and wait until they have exited (up to 10 s each, then SIGKILL) so the ports are free on return.
# Usage: bash scripts/stop-local.sh
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pid_file="$root/runtime/processes-unix.txt"
if [[ ! -f "$pid_file" ]]; then
  echo "No services recorded by scripts/start-local.sh."
  exit 0
fi
stopping=()
while IFS=$'\t' read -r pid start; do
  [[ -n "$pid" ]] || continue
  if kill -0 "$pid" 2>/dev/null && [[ "$(ps -o lstart= -p "$pid" 2>/dev/null | sed 's/^ *//')" == "$start" ]]; then
    kill "$pid"
    stopping+=("$pid")
  fi
done < "$pid_file"
# ${arr[@]+...} keeps empty arrays safe under `set -u` on macOS's bash 3.2.
for pid in ${stopping[@]+"${stopping[@]}"}; do
  for _ in $(seq 1 50); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.2
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
    echo "Process $pid did not exit within 10 s and was killed."
  else
    echo "Stopped process $pid."
  fi
done
rm -f "$pid_file"
