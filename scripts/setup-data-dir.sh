#!/usr/bin/env bash
# Create the ExLibris data directory and optionally migrate legacy paths.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="$ROOT/data"

mkdir -p "$DATA"

if [[ -f "$ROOT/library.db" && ! -f "$DATA/library.db" ]]; then
  echo "Moving library.db -> data/"
  mv "$ROOT/library.db" "$DATA/"
fi

for wal in library.db-wal library.db-shm library.db-journal; do
  if [[ -f "$ROOT/$wal" && ! -e "$DATA/$wal" ]]; then
    echo "Moving $wal -> data/"
    mv "$ROOT/$wal" "$DATA/"
  fi
done

if [[ -d "$ROOT/covers" ]]; then
  mkdir -p "$DATA/covers"
  if mountpoint -q "$ROOT/covers" 2>/dev/null; then
    :
  elif [[ -z "$(ls -A "$DATA/covers" 2>/dev/null)" ]]; then
    echo "Moving covers/ -> data/covers/"
    shopt -s dotglob
    mv "$ROOT/covers"/* "$DATA/covers/" 2>/dev/null || true
    shopt -u dotglob
    rmdir "$ROOT/covers" 2>/dev/null || true
  fi
fi

for script in "$ROOT/scan_books.py" "$ROOT/update_epubs.py" "$ROOT/manage_users.py" "$ROOT/cleanup_library.py" "$ROOT/serve_web.py" "$ROOT/scripts/shard-covers.py" "$ROOT/scripts/convert-epubs-in-dir.py" "$ROOT"/scripts/*.sh "$ROOT"/web/cgi-bin/*.py; do
  if [[ -f "$script" ]] && head -1 "$script" | grep -q '^#!'; then
    chmod +x "$script"
  fi
done

echo "Data directory: $DATA"
if [[ ! -f "$ROOT/admins.txt" ]]; then
  echo ""
  echo "Optional: cp admins.txt.example admins.txt  # then add admin usernames"
fi
echo ""
echo "For Apache (www-data), grant group write on data/ only:"
echo "  sudo usermod -aG \"$(id -gn)\" www-data"
echo "  sudo chgrp \"$(id -gn)\" \"$DATA\""
echo "  chmod 2775 \"$DATA\""
echo "  chmod g+rX \"$ROOT\" \"$ROOT/web\" \"$ROOT/exlibris\" /media/books"
echo ""
echo "Reload Apache after updating /etc/apache2/conf-available/exlibris.conf"
echo "so EXLIBRIS_DATABASE_PATH points at: $DATA/library.db"
