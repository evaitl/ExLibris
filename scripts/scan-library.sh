#!/usr/bin/env bash
# Run an incremental library scan (for cron or manual use).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/data/scan.log"

mkdir -p "$ROOT/data"

{
  echo "=== $(date -Is) scan start ==="
  if [[ -f "$ROOT/.venv/bin/activate" ]]; then
    # shellcheck source=/dev/null
    source "$ROOT/.venv/bin/activate"
  fi
  cd "$ROOT"
  if command -v exlibris >/dev/null 2>&1; then
    exlibris scan
  else
    "$ROOT/.venv/bin/exlibris" scan
  fi
  echo "=== $(date -Is) scan finished ==="
} >>"$LOG" 2>&1
