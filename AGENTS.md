# AGENTS.md

ExLibris scans directory trees for EPUBs, stores metadata in SQLite, and serves a
CGI web UI to browse the collection. See `README.md` for user-facing docs and
`DEVELOPMENT.md` for architecture/history.

## Cursor Cloud specific instructions

Two Python paths, on purpose:

- **Web UI (browsing/CGI)** uses only the Python standard library — no venv needed to serve pages.
- **Scanner / CLI (`exlibris`, `scan_books.py`, `classify.py`, `cleanup_library.py`, `update_epubs.py`)** needs the project venv (`typer`, `sqlalchemy`). These scripts auto re-exec with `.venv/bin/python` when run under system Python, so always keep `.venv` present.

Environment notes:

- The update script creates `.venv` and installs the package (`pip install -e .`) plus `pytest`. Activate it or call binaries via `.venv/bin/...`.
- **Calibre is a system dependency** (`ebook-meta`, `ebook-convert`, `fetch-ebook-metadata` on `PATH`) required for scanning, EPUB conversion, and online metadata fetch. It is intentionally NOT in the update script (large, brittle). If missing, install with `sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -o Dpkg::Options::="--force-confold" calibre` — the `--force-confold` flag avoids an interactive `fuse3` conffile prompt that otherwise hangs the install. Tests do NOT need Calibre (they mock the subprocess calls).
- Books live under `/media/books` by default (outside the repo, gitignored path). Create it and drop `.epub` files there before scanning: `sudo mkdir -p /media/books`. You can synthesize test EPUBs with `ebook-convert some.html "/media/books/Title.epub" --title ... --authors ...`.
- Runtime data (`data/library.db`, `data/covers/`) is gitignored. Run `./scripts/setup-data-dir.sh` once to create it. The DB is created/migrated automatically on first scan or CLI use.
- Only one long-running maintenance job (scan / cleanup / update_epubs / classify) can run at a time — they share an exclusive `flock` on `data/library.lock`.
- `admins.txt` (gitignored; copy from `admins.txt.example`) lists usernames allowed to use admin curation actions (edit metadata, fetch metadata, restore cover, upload cover).

Common commands:

- Test: `.venv/bin/python -m pytest` (fast, ~1s; mocks Calibre).
- Scan: `.venv/bin/python scan_books.py` (or `.venv/bin/exlibris scan`).
- Classify genres: `./classify.py` (dry run) then `./classify.py --execute` (or `.venv/bin/exlibris classify`).
- Serve web UI: `.venv/bin/python serve_web.py` (or `.venv/bin/exlibris serve`) → http://127.0.0.1:8080/. Covers are served as static files under `/covers/`, CGI under `/cgi-bin/`, assets under `/static/`.
- Create a user: `.venv/bin/exlibris user create NAME` (prompts for password, or pass `--password`).

No linter/formatter is configured in this repo (no ruff/flake8/black config, no pre-commit hooks).
