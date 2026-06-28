# ExLibris Development Log

This document records design and implementation history. It captures what was built, why, and how the project evolved through iterative requests.

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
  scan.log
scan_books.py       ← standalone scan entry point
exlibris/
  schema/           ← SQL migrations (001–006)
  models.py         ← SQLAlchemy ORM
  database.py       ← init, migrations, WAL mode, upsert
  auth.py           ← scrypt passwords, signed session cookies
  users.py          ← user CRUD, favorites
  ebook_meta.py     ← Calibre ebook-meta wrapper
  fetch_metadata.py ← online metadata fetch, restore embedded cover
  file_hash.py      ← SHA-1 for duplicate detection
  scanner.py        ← directory walk, per-book commit, dedup by hash
  cgi/
    common.py       ← queries, FTS-backed list_books, auth, favorites
    search.py       ← FTS5 MATCH expression builder
    render.py       ← HTML templates
apache/
  exlibris.conf     ← path-based Apache config (/exlibris/)
scripts/
  setup-data-dir.sh ← create/migrate data/ directory
  scan-library.sh   ← cron-friendly scan wrapper
web/
  cgi-bin/          ← index, book, cover, download, fetch_metadata, restore_cover,
                    ←   login, logout, register, favorite, edit_book
  static/
    style.css
    library.js      ← debounced search, keyboard shortcuts, sort arrows
```

### Data flow

1. **Scan:** walks `/media/books` (or configured paths), SHA-1 each file, skips duplicates, calls `ebook-meta`, upserts `data/library.db`, saves covers to `data/covers/`.
2. **Browse:** CGI scripts read the database and render HTML with server-side pagination.
3. **Download:** serves EPUBs only if the path is under a configured scan directory.
4. **Fetch metadata online:** queries Calibre `fetch-ebook-metadata`, updates the database and cover images only (EPUB files unchanged).

---

## Session 1 — Initial build (June 2026)

### Bootstrap through first release

- SQLite schema with FTS5 (`001_initial.sql`)
- Scanner using Calibre `ebook-meta` (replaced pure-Python extractors)
- CGI web UI: library grid, book detail, covers, download
- Filters: title, author, publisher, genre (tags), language
- Covers (`002_covers.sql`), tags (`003_tags.sql`)
- EPUB-only scanner; FastAPI viewer removed in favor of CGI only
- Incremental scan with WAL mode for live UI updates during long scans
- Sort by title, author, published date, size, last scanned
- Apache path-based deployment documented
- Initial push to https://github.com/evaitl/ExLibris.git

### Post-release (commits through `935492d`)

- **SHA-1 dedup:** `004_content_hash.sql`; scanner skips duplicate files
- **Fetch metadata online:** CGI button; DB/cover updates only
- **Dedicated `data/` directory** with `scripts/setup-data-dir.sh`
- **Default books path:** `/media/books`
- **`apache/exlibris.conf`** for `/exlibris/` mount
- **Removed dead code:** FastAPI, `exlibris/extractors/`, broken `httpd.conf`
- **Cron:** `scripts/scan-library.sh` for nightly scans; logs to `data/scan.log`
- **CGI without pydantic:** `exlibris/cgi/common.py` avoids importing `exlibris.config` so Apache system Python works for the web UI

---

## Session 2 — Server deployment and UI polish (June 2026)

This session continued after the initial release, deploying to a production server and iterating on the library UI for a large collection (~600K books planned).

### Server deployment (Apache on Debian)

**Install path:** `/media/books/ExLibris` (not under `$HOME`).

**Apache logs:** `/var/log/apache2/error.log` and `access.log`.

**Problems encountered and fixes:**

| Symptom | Cause | Fix |
|---------|-------|-----|
| `AH01630: client denied … /home/evaitl/programming` | `EXLIBRIS_ROOT` in copied `exlibris.conf` still pointed at dev machine path | Set `Define EXLIBRIS_ROOT /media/books/ExLibris` in `/etc/apache2/conf-available/exlibris.conf` |
| `AH01630` under `/home/...` | `www-data` cannot traverse home directory | Move install to `/media/books/ExLibris` or `chmod o+x` on parent dirs |
| `ScriptAlias … will probably never match` | Broad `Alias /exlibris/` listed before `ScriptAlias` | Put `ScriptAlias` **before** `Alias` in config |
| `ModuleNotFoundError: No module named 'pydantic'` on fetch | `allowed_book_file()` imported `exlibris.config` | Removed pydantic from CGI code path (`common.py`, `fetch_metadata.py`) |
| Database read-only for web server | `library.db` files owned by user, mode `644` | `sudo usermod -aG evaitl www-data`; `chmod g+rw data/library.db*` |
| Fetch replaces cover with blank | Open Library returns 1×1 transparent GIF (~807 bytes) when no cover | See § Fetch metadata cover handling below |

**Permissions checklist:**

```bash
# Apache reads: /media/books, web/, exlibris/
# Apache writes: data/ only
sudo usermod -aG evaitl www-data
chmod 2775 data/ data/covers
chmod g+rw data/library.db data/library.db-wal data/library.db-shm
sudo systemctl restart apache2   # pick up new group
```

**Scanner vs web UI Python:**

- **Web UI (CGI):** system Python 3.11+ — stdlib only
- **Scanner (`exlibris scan`):** project venv with `pip install -e .`
- **Cron:** `scripts/scan-library.sh` activates `.venv` automatically

### Book detail display

- **Published date:** formatted for display (e.g. `15 June 2020`) without time component
- **File name:** basename shown in metadata grid (not full path)

### Search and filters

**Full-text search (FTS5):** title, author, publisher, and genre filters use `books_fts` with prefix token matching. Language and favorites use ordinary SQL `WHERE` clauses. Migration `006_fts_extend.sql` adds `sort_title` and `tags` to the FTS index and rebuilds it from `books` — no EPUB rescan required.

**Word-based matching:** input split on spaces; every word must match within its filter field. FTS uses `{columns} : ("word"*)` clauses combined with `AND`. Falls back to case-insensitive `LIKE` substring search if FTS query construction fails (e.g. punctuation-only input).

- Title: `title`, `sort_title`, `file_name`
- Author, publisher, genre: `authors`, `publisher`, `tags`

Example: `j k rowling` matches `J. K. Rowling` and `Rowling, J. K.`

### Pagination and scale (~600K books)

**Decision:** server-side pagination, not infinite scroll. Search/filter to narrow results; avoid loading entire library.

- Default page shows first page of all books (sorted by dropdown)
- **Page size:** 10, 25, 50, 100 (default), 200 — configurable in UI
- **Jump to page** control in pagination bar
- Filtered `COUNT(*)` used for pagination totals
- `LIMIT`/`OFFSET` in SQL queries

**Not yet done (recommended for scale):**

- Keyset pagination for very deep offsets

### Random sort

**Random** sort option samples up to `page_size` books via `ORDER BY RANDOM()`. Each page request returns a new random batch; pagination counter is informational only.

### Sort direction

↑/↓ arrows next to sort dropdown toggle ascending/descending. Defaults: title/author asc; published/size/scanned desc. Hidden for Random sort.

### Keyboard shortcuts (`web/static/library.js`)

| Key | Action |
|-----|--------|
| `/` | Focus title search |
| `Esc` | Clear filters (closes help dialog first) |
| `?` | Toggle shortcuts help |
| `←` / `→` | Previous / next page |
| Page Up / Page Down | Native browser scroll |

### Debounced search

Text filter fields auto-submit **1 second** after typing stops. Language, sort, and per-page dropdowns submit on change. **Apply** still available for immediate submit.

### Compact toolbar layout

- Header: brand + tagline on one line, reduced padding
- Filters: single compact row (placeholders instead of labels)
- Stats line inside filter box

### Layout and theme

- Container widened: `1680px` max, `0.5rem` side margins
- Apply button: fixed `width: auto` (was squeezed by `width: 100%`)
- **Accent color:** dark blue (`#1a365d` light / `#6ba3d6` dark) with cool gray-blue neutrals

### Fetch metadata cover handling

When online fetch finds no real cover, **existing cover is preserved**.

Detection in `exlibris/fetch_metadata.py`:

1. Open Library URLs use `?default=false` → 404 when no cover (instead of blank image)
2. Downloaded images must be ≥ 100×100 pixels (rejects 1×1 transparent GIF placeholder)
3. `_save_cover()` only runs when valid bytes returned; metadata fields still update

---

## Database Schema (current)

| Version | File | Change |
|---------|------|--------|
| 1 | `001_initial.sql` | `books`, `books_fts`, indexes |
| 2 | `002_covers.sql` | `cover_path` column |
| 3 | `003_tags.sql` | `tags` column (genre/subjects) |
| 4 | `004_content_hash.sql` | unique index on `content_hash` for dedup |
| 5 | `005_users.sql` | `users`, `user_favorites` |
| 6 | `006_fts_extend.sql` | extend `books_fts` with `sort_title`, `tags`; rebuild index |

Key `books` columns: `file_path`, `content_hash`, `title`, `authors`, `publisher`, `published_date`, `language`, `description`, `tags`, `cover_path`, `first_seen_at`, `last_scanned_at`, `is_missing`.

---

## Dependencies

**Python (pyproject.toml):** typer, pydantic-settings, sqlalchemy, pyyaml

**System:** Calibre (`ebook-meta`, `fetch-ebook-metadata` on PATH)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

On Debian/Ubuntu: `sudo apt install python3-venv python3-pip calibre`

---

## CLI and cron

```bash
exlibris scan                         # scan default /media/books (also runs DB migrations)
exlibris scan --path ~/foo              # additional paths
exlibris user create NAME               # create web login (scrypt password hash)
python scan_books.py -v                 # standalone scanner, verbose
python scan_books.py -q                 # scan without per-file progress
./scripts/setup-data-dir.sh             # create data/ directory; chmod entry-point scripts
./scripts/scan-library.sh               # manual or cron scan
```

Cron example (4 AM daily):

```cron
0 4 * * * /path/to/ExLibris/scripts/scan-library.sh
```

---

## Session 3 — Accounts, curation, FTS (June 2026)

- **User accounts:** `005_users.sql`; scrypt passwords; login/logout/register CGI; `exlibris user create`
- **Favorites:** per-user `user_favorites`; checkbox on detail page; **Favorites only** library filter (login required)
- **Cover curation:** reject Google/Open Library placeholder images; **restore cover from file**; manual **edit title & author**
- **Scan:** per-file progress; skip unchanged files before Calibre; mark books missing when absent from scanned paths (metadata retained)
- **FTS search:** `006_fts_extend.sql`; `exlibris/cgi/search.py`; library UI uses `books_fts MATCH`
- **Toolbar:** flex layout fix; narrower language dropdown
- **Scripts:** `scan_books.py`, `scripts/*.sh`, all shebang CGI entry points marked executable in git

---

## Commit history (Session 2)

| Commit | Summary |
|--------|---------|
| `9131d4d` | Published date formatting; file name on detail page |
| `bcd6369` | Word-based text search for title/author/genre/publisher |
| `3613f6f` | Pagination, Page Up/Down browse, random sort |
| `b2c9bc0` | Default page shows books without requiring filters |
| `2b47d66` | Sort direction, page size, debounced search, keyboard shortcuts |
| `d91cec5` | Compact header and filter toolbar |
| `9cfadda` | Arrow keys for page navigation; Page Up/Down scrolls |
| `07d5e1f` | Search debounce increased to 1 second |
| `8ce827c` | Wider layout; Apply button fix; dark blue theme |
| `533f39c` | Reject placeholder covers on online metadata fetch |

---

## Known State

- **UI:** CGI only; minimal JS in `library.js`
- **Books:** default scan `/media/books`; runtime data in `data/`
- **Dedup:** SHA-1 `content_hash`; scanner skips unchanged files before Calibre; missing files hidden from browse
- **Library browse:** paginated FTS search, favorites filter, random sort, keyboard navigation
- **Accounts:** optional login for favorites only; web or CLI registration
- **Curation:** edit title/author, fetch metadata, restore embedded cover
- **Fetch metadata:** DB + covers only; placeholders rejected; existing cover kept when no new image
- **Apache:** path mount `/exlibris/`; `EXLIBRIS_ROOT` must match server install path
- **Scale target:** ~600K books — FTS + pagination in place; keyset pagination still open

---

## Possible Follow-ups

- Genre/language links from book detail back to filtered library
- Scan status in header (`data/scan.log` or DB)
- Faceted filter counts (e.g. language with book counts)
- Keyset pagination for deep pages
- Favorite indicator on library grid cards
- Optional disable open registration; protect metadata edits behind login
- Extend manual edit to publisher, tags, series

---

## Conversation Reference

Cursor agent session, June 2026 — initial build (Session 1) and server deployment / UI iteration (Session 2).

Transcript: `44633832-6442-49a7-aa75-ffee74ac2a80`
