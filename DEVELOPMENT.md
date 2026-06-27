# ExLibris Development Log

This document records the design and implementation history from the initial build session (June 2026). It captures what was built, why, and how the project evolved through iterative requests.

**Repository:** https://github.com/evaitl/ExLibris  
**Initial commit:** `b23d9f2` — *Initial ExLibris release: EPUB library scanner and web UI.*

---

## Goal

Build a personal ebook library viewer that:

1. Walks designated directory trees for ebook files
2. Extracts metadata into a SQLite database
3. Serves a web UI to browse, filter, and download books

The scanner and web server are separate concerns: scan once (or incrementally), browse anytime.

---

## Architecture (current)

```
/media/books/       ← default EPUB library (outside repo)
data/               ← runtime data (gitignored)
  library.db
  covers/
scan_books.py       ← standalone scan entry point
exlibris/
  schema/           ← SQL migrations (001–004)
  models.py         ← SQLAlchemy ORM
  database.py       ← init, migrations, WAL mode, upsert
  ebook_meta.py     ← Calibre ebook-meta wrapper
  fetch_metadata.py ← online metadata fetch (DB only)
  file_hash.py      ← SHA-1 for duplicate detection
  scanner.py        ← directory walk, per-book commit, dedup by hash
  cgi/              ← shared query/render helpers for CGI
apache/
  exlibris.conf     ← path-based Apache config (/exlibris/)
scripts/
  setup-data-dir.sh ← create/migrate data/ directory
web/
  cgi-bin/          ← index, book, cover, download, fetch_metadata
  static/style.css
```

### Data flow

1. **Scan:** walks `/media/books` (or configured paths), SHA-1 each file, skips duplicates, calls `ebook-meta`, upserts `data/library.db`, saves covers to `data/covers/`.
2. **Browse:** CGI scripts read the database and render HTML.
3. **Download:** serves EPUBs only if the path is under a configured scan directory.
4. **Fetch metadata online:** queries Calibre `fetch-ebook-metadata`, updates the database and cover images only (EPUB files unchanged).

---

## Session Timeline

### 1. Project bootstrap

**Request:** Scan epub, mobi, azw3, and pdf; store metadata in SQLite; display via web server.

Initial scaffolding included Typer CLI, SQLAlchemy, format-specific extractors (ebooklib, pypdf, ebookatty), and a basic library UI. An early FastAPI viewer was later removed in favor of CGI only.

**Pause:** User asked to hold off before installing dependencies.

### 2. SQLite schema

**Request:** Start with an appropriate SQLite schema.

Created `exlibris/schema/001_initial.sql`:

- `schema_version` — migration tracking
- `books` — one row per file (path, format, size, mtime, hash, bibliographic fields, scan lifecycle)
- `books_fts` — FTS5 virtual table with sync triggers for full-text search
- Indexes on title, authors, format, ISBN, series, scan time, missing flag

SQLAlchemy models in `exlibris/models.py`; `database.py` applies migrations on init.

### 3. Scanner with ebook-meta

**Request:** Python script to walk directories and populate the database using Calibre `ebook-meta`.

Added:

- `exlibris/ebook_meta.py` — parses OPF/XML output from `ebook-meta`
- `scan_books.py` — CLI wrapper with verbose logging
- Refactored `exlibris/scanner.py` to use ebook-meta instead of pure-Python extractors

Extractors under `exlibris/extractors/` remain in the tree but the live scanner path uses ebook-meta only.

### 4. License

**Request:** Blue Oak Model License 1.0.0.

Added `LICENSE.md` (moved from initial `LICENSE` filename per follow-up).

### 5. CGI web UI

**Request:** HTML/CSS with Python CGI backend.

Built:

- `web/cgi-bin/index.py` — library grid with filters and pagination
- `web/cgi-bin/book.py` — book detail page
- `web/cgi-bin/cover.py` — serve cover images
- `web/static/style.css` — card grid, filters, detail layout
- `exlibris/cgi/common.py` — DB queries, env-based config
- `exlibris/cgi/render.py` — HTML generation

**Environment variables:**

| Variable | Purpose |
|----------|---------|
| `EXLIBRIS_DATABASE_PATH` | Path to `library.db` |
| `EXLIBRIS_CGI_PREFIX` | URL prefix for CGI scripts (e.g. `/cgi-bin/`) |
| `EXLIBRIS_STATIC_URL` | Stylesheet URL |

Dev server example:

```bash
cd web
EXLIBRIS_CGI_PREFIX=/cgi-bin/ \
EXLIBRIS_STATIC_URL=/static/style.css \
EXLIBRIS_DATABASE_PATH=/path/to/library.db \
python3 -m http.server --cgi 8080
```

### 6. Default scan path and gitignore

**Request:** Scan `books/` by default; gitignore the books directory.

- Default scan path: `books/` in project root
- `.gitignore`: `books/`, `covers/`, `library.db`, WAL/journal files, `scan.log`, `.venv/`

### 7. First scan and browser preview

Scanner was run against `books/` (~7,511 EPUBs). CGI dev server started on port 8080.

### 8. Book covers

**Request:** Display covers on the web page.

- Migration `002_covers.sql` — `cover_path` column on `books`
- Scanner extracts covers via `ebook-meta --cover` into `covers/{id}.jpg`
- `cover.py` CGI endpoint serves cover images

### 9. Filters

**Request:** Filter by genre, title, author, publisher.

- Migration `003_tags.sql` — `tags` column (from Calibre tags / OPF subjects)
- Filter UI: title, author, publisher, genre (tags), language
- **Title/author:** text inputs, case-insensitive substring match
- **Publisher, genre, language:** `<select>` dropdowns populated from distinct DB values
- Genre and publisher option labels truncated to 48 characters; CSS `max-width: 14rem` on selects to prevent layout overlap

### 10. Book detail improvements

**Request:** Remove file/path fields; add download button.

- Detail page shows bibliographic metadata only
- `download.py` serves EPUB with `Content-Disposition: attachment`; path validated under `books/`

### 11. Description HTML

**Request:** Don't escape HTML in descriptions (e.g. Doctorow books with markup in OPF).

- Description rendered as raw HTML in detail view (`description__body`)
- Trust model: metadata comes from user's own files

### 12. Database reset and rescan

**Request:** Clear database and rescan.

Deleted `library.db` and restarted full scan of `books/`.

### 13. Language filter

**Request:** Add language filter to the web page.

Added language `<select>` to the CGI library view.

### 14. README

**Request:** Installation and run instructions in README.md.

Documented: requirements (Python 3.11+, Calibre), venv setup, scanning, CGI dev server, Apache deployment, CLI usage.

### 15. EPUB only

**Request:** Scanner looks for `.epub` only; remove format filter/sort from web UI.

- `SUPPORTED_EXTENSIONS = {".epub"}` in `scanner.py`
- Removed format filter and format sort option from library page

Stopped in-progress multi-format scan and restarted with EPUB-only scanner.

### 16. Apache instructions

**Request:** Document running under Apache.

Added Apache/mod_cgi configuration section to README.md.

### 17. Runtime artifacts in gitignore

**Request:** Ignore runtime files.

Added to `.gitignore`:

```
library.db-journal
library.db-wal
library.db-shm
scan.log
```

### 18. Incremental scan (live updates)

**Request:** Process one book at a time so the web UI updates during a long scan.

Changes:

- Scanner commits after each book (rollback on per-book errors)
- SQLite WAL mode enabled in `database.py` for concurrent reads during writes
- `-v` / `--verbose` prints `indexed: {title}` per book

Killed batch-commit scan and restarted with incremental scanner.

### 19. Sort by published date

**Request:** Add published date to sort options.

Sort order: `published_date IS NULL, published_date DESC` (newest first; undated books last).

Added to the CGI library page.

Available sort fields: title, author, date added, published date.

### 20. Initial commit and push

**Request:** Commit to main and push.

Committed 36 source files; pushed to `origin/main` at https://github.com/evaitl/ExLibris.git. Runtime artifacts excluded via `.gitignore`.

### 21. Post-release improvements (June 2026)

- **SHA-1 dedup:** migration `004_content_hash.sql`; scanner skips duplicate files
- **Fetch metadata online:** CGI button + `fetch_metadata.py`; DB/cover updates only (preserves dedup)
- **Dedicated `data/` directory:** `library.db` and `covers/` moved out of repo root; `scripts/setup-data-dir.sh`
- **Default books path:** `/media/books` instead of project `books/`
- **Apache:** `apache/exlibris.conf` for path-based `/exlibris/` deployment
- **Removed dead code:** FastAPI app/templates, `exlibris/extractors/`, unused `ebook_meta` write helpers
- **Fetch UX:** loading state on fetch button; headless Calibre env for server use

---

## Database Schema (current)

| Version | File | Change |
|---------|------|--------|
| 1 | `001_initial.sql` | `books`, `books_fts`, indexes |
| 2 | `002_covers.sql` | `cover_path` column |
| 3 | `003_tags.sql` | `tags` column (genre/subjects) |
| 4 | `004_content_hash.sql` | unique index on `content_hash` for dedup |

Key `books` columns: `file_path`, `content_hash`, `title`, `authors`, `publisher`, `published_date`, `language`, `description`, `tags`, `cover_path`, `first_seen_at`, `last_scanned_at`, `is_missing`.

---

## Dependencies

**Python (pyproject.toml):** typer, pydantic-settings, sqlalchemy, pyyaml

**System:** Calibre (`ebook-meta` on PATH)

Install:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

On Debian/Ubuntu: `sudo apt install python3-venv python3-pip calibre`

---

## CLI

```bash
exlibris scan                    # scan default /media/books
exlibris scan --path ~/foo       # scan additional paths
python scan_books.py -v          # standalone scanner, verbose
./scripts/setup-data-dir.sh      # create data/ directory
```

---

## Known State

- **UI:** CGI only (`web/cgi-bin/`); FastAPI viewer removed
- **Books:** default scan path `/media/books`; runtime data in `data/`
- **Dedup:** SHA-1 `content_hash`; scanner skips files already in DB
- **Fetch metadata:** updates SQLite + `data/covers/` only; button shows “Fetching…”
- **Apache:** path-based mount at `/exlibris/` via `apache/exlibris.conf`

---

## Possible Follow-ups

- Progress indicator or scan status API
- Full-text search wired into the web UI (FTS table exists but UI uses LIKE filters)
- Pagination on the library index page
- Resumable or parallel scanning for very large libraries
- Apache setup verification on target host

---

## Conversation Reference

Full agent transcript: Cursor chat session that produced this project (June 2026).
