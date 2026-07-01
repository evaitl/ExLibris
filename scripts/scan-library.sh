#!/usr/bin/env bash
# Run an incremental library scan (for cron or manual use).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/data/scan.log"

mkdir -p "$ROOT/data"

run_exlibris() {
  if command -v exlibris >/dev/null 2>&1; then
    exlibris "$@"
  else
    "$ROOT/.venv/bin/exlibris" "$@"
  fi
}

run_cleanup() {
  if [[ -x "$ROOT/cleanup_library.py" ]]; then
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
