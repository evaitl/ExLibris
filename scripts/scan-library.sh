#!/usr/bin/env bash
# Run an incremental library scan (for cron or manual use).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/data/scan.log"
LOCK="$ROOT/data/library.lock"

mkdir -p "$ROOT/data"

exec 9>"$LOCK"
if ! flock -n 9; then
  echo "=== $(date -Is) skipped: another library job is running ===" >>"$LOG"
  exit 0
fi
echo $$ >&9
export EXLIBRIS_JOB_LOCK_HELD=1

run_exlibris() {
  if command -v exlibris >/dev/null 2>&1; then
    exlibris "$@"
  else
    "$ROOT/.venv/bin/exlibris" "$@"
  fi
}

run_cleanup() {
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    "$ROOT/.venv/bin/python" "$ROOT/cleanup_library.py" "$@"
  elif [[ -x "$ROOT/cleanup_library.py" ]]; then
    "$ROOT/cleanup_library.py" "$@"
  elif command -v python3 >/dev/null 2>&1; then
    python3 "$ROOT/cleanup_library.py" "$@"
  else
    echo "cleanup_library.py not found" >&2
    return 1
  fi
}

{
  echo "=== $(date -Is) scan start ==="
  if [[ -f "$ROOT/.venv/bin/activate" ]]; then
    # shellcheck source=/dev/null
    source "$ROOT/.venv/bin/activate"
  fi
  cd "$ROOT"
  run_exlibris scan
  echo "=== $(date -Is) scan finished ==="
  echo "=== $(date -Is) cleanup start ==="
  run_cleanup run --execute --backfill-hashes --prune-empty-dirs
  echo "=== $(date -Is) cleanup finished ==="
} >>"$LOG" 2>&1
