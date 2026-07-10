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
cleanup_library.py  ← audit/reconcile files vs database
manage_users.py     ← list/delete web accounts (stdlib)
exlibris/
  schema/           ← SQL migrations (001–009)
  models.py         ← SQLAlchemy ORM
  database.py       ← init, migrations, WAL mode, upsert
  auth.py           ← scrypt passwords, signed session cookies
  users.py          ← user CRUD, favorites
  admins.py         ← reads admins.txt for curation permissions
  ebook_meta.py     ← Calibre ebook-meta wrapper
  fetch_metadata.py ← online metadata fetch, restore embedded cover
  file_hash.py      ← SHA-1 for duplicate detection
  book_paths.py     ← EPUB file walk (stdlib; shared by scanner and cleanup)
  cover_paths.py    ← sharded cover layout (data/covers/NN/{id}.jpg)
  filenames.py      ← safe basename sanitization; metadata-based rename helpers
  epub_validate.py  ← EPUB ZIP/OPF/spine validation (stdlib; optional Calibre deep check)
  cleanup.py        ← library file/DB reconciliation helpers
  scanner.py        ← directory walk, per-book commit, dedup by hash
  cgi/
    common.py       ← queries, FTS-backed list_books, auth, favorites
    search.py       ← FTS5 MATCH expression builder
    render.py       ← HTML templates
apache/
  exlibris.conf     ← path-based Apache config (/exlibris/)
scripts/
  setup-data-dir.sh ← create/migrate data/ directory; chmod entry points
  shard-covers.py   ← move flat covers into NN/ shards; update cover_path
  rename-short-files.py ← thin wrapper around cleanup filename sanitization
  scan-library.sh   ← cron: scan then cleanup (backfill hashes, prune dirs)
web/
  cgi-bin/          ← index, book, cover, download, fetch_metadata, restore_cover,
                    ←   login, logout, register, favorite, edit_book
  static/
    style.css
    library.js      ← debounced search, keyboard shortcuts, sort arrows
    detail.js       ← arrow keys on book detail pages
    swipe-nav.js    ← touch swipe prev/next (library and detail)
```

`admins.txt` at the project root lists administrator usernames (gitignored; copy from `admins.txt.example`). Used for metadata edit, fetch metadata, and restore cover.

### Data flow

1. **Scan:** walks configured paths, skips unchanged files by size/mtime, SHA-1 when needed, calls `ebook-meta`, upserts `data/library.db`, marks missing files when absent from scan roots; repoints canonical row when duplicate has longer basename and deletes the old shorter file under scan roots.
2. **Cleanup:** compares scan roots to DB — sanitizes filenames, dedupes by SHA-1 (longest basename), optionally validates EPUB structure and removes corrupt files (`--validate-epubs`), indexes new EPUBs, optional hash backfill, prune empty dirs, optional hard purge (`--force-clean`).
3. **Browse:** CGI scripts read the database; FTS5 search with server-side pagination; cover images served as static files under `/exlibris/covers/` (sharded on disk as `data/covers/NN/{id}.jpg`).
4. **Download:** serves EPUBs only if the path is under a configured scan directory.
5. **Curation (admins only):** fetch metadata online, restore embedded cover, manual title/author/genre edit — all update the database/covers only.

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
- Sort by title, author, published date, size, page count, last scanned
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

- Title: `title`, `sort_title`, `file_name` (filenames often include author names)
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

↑/↓ arrows next to sort dropdown toggle ascending/descending. Defaults: title/author asc; published/size/pages/scanned desc. Hidden for Random sort.

### Keyboard shortcuts (`web/static/library.js`)

| Key | Action |
|-----|--------|
| `/` | Focus title search |
| `Esc` | Clear filters (closes help dialog first) |
| `?` | Toggle shortcuts help |
| `←` / `→` | Previous / next page (library) or book (detail) |
| Swipe ← / → | Same as arrow keys on touch devices (`swipe-nav.js`) |
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

## CLI, maintenance scripts, and cron

```bash
exlibris scan                              # scan default /media/books (runs DB migrations)
exlibris scan --path ~/foo                 # additional paths
exlibris user create NAME                  # create web login (scrypt password hash)
exlibris cleanup audit                     # read-only file vs DB report
exlibris cleanup run --execute             # dedupe, index new EPUBs (dry-run without --execute)
exlibris cleanup run --execute \
  --backfill-hashes --prune-empty-dirs     # also fill NULL content_hash, prune empty dirs
exlibris cleanup run --execute --strip-description-html  # plain-text DB descriptions
exlibris cleanup run --execute --validate-epubs  # remove corrupt EPUBs + DB rows (destructive)
exlibris cleanup audit --validate-epubs-only     # report invalid EPUBs only (read-only)
exlibris cleanup run --execute --validate-epubs-only  # remove bad EPUBs; skip other cleanup
exlibris cleanup run --execute --force-clean  # hard-delete rows whose file is gone
python scan_books.py -v                    # standalone scanner, verbose
./manage_users.py list                     # list accounts (stdlib; no venv)
./scripts/setup-data-dir.sh                # create data/; chmod entry-point scripts
./scripts/shard-covers.py                  # dry-run: shard flat covers into NN/ subdirs
./scripts/shard-covers.py --execute        # move covers; update books.cover_path
./scripts/scan-library.sh                  # manual or cron: scan + cleanup
```

Cron example (4 AM daily) — runs scan then cleanup with backfill and prune:

```cron
0 4 * * * /path/to/ExLibris/scripts/scan-library.sh
```

Log: `data/scan.log`

### Library cleanup (`cleanup_library.py` / `exlibris cleanup`)

Dry-run by default; `--execute` applies changes. `--force-clean` requires `--execute` (destructive: deletes book rows, cascades favorites, removes orphan covers).

| Phase | Behavior |
|-------|----------|
| Audit | Unindexed files, duplicate groups, new files, absent rows, orphan covers, NULL hashes, out-of-root paths, filename fixes, invalid EPUBs (with `--validate-epubs`) |
| Sanitize | Every `run`: strip unsafe characters; rename stems &lt; 10 chars to `{title} - {authors}-({publisher}).epub`; update `file_path` / `file_name` |
| Validate | `--validate-epubs`: ZIP + container + OPF spine checks (stdlib); `--validate-epubs-deep` also runs `ebook-meta` |
| Validate only | `--validate-epubs-only`: skip dedupe, sanitize, index; only validate (and remove with `--execute`) |
| Remove invalid | With `run --execute --validate-epubs`: delete bad files under scan roots; `purge_book` + cover removal for indexed rows |
| Dedupe | SHA-1 match → keep longest basename; delete shorter copies; repoint DB (no Calibre) |
| Index | Unindexed files with no hash match → `scan_single_file()` (Calibre + cover); scan and cleanup indexing validate EPUB structure first and delete invalid files under scan roots |
| Backfill | `--backfill-hashes`: SHA-1 for rows with `content_hash IS NULL` |
| Descriptions | `--strip-description-html`: remove HTML tags and decode HTML entities in `books.description` (commits per row) |

**Description cleanup** (`exlibris/description_text.py`): decodes HTML entities (including double-encoded), strips tags (script/style content dropped), collapses whitespace, and sets empty results to `NULL`. The web UI still escapes descriptions as plain text; this option cleans stored metadata for readability and search. Dry-run by default; use `run --execute --strip-description-html`. Not part of default cron.

| Prune | `--prune-empty-dirs`: remove empty directories under scan roots |
| Purge | `--force-clean`: DELETE rows whose `file_path` is not a regular file |

`audit` and dedupe/repoint use sqlite3 + `sha1_file` (no venv). Indexing new files needs venv + Calibre.

### EPUB validation (`exlibris/epub_validate.py`)

Structural validation only — not malware scanning. Checks:

- ZIP integrity (`testzip`, member reads; catches corrupt/truncated archives)
- `META-INF/container.xml` and OPF manifest/spine
- Spine HTML/XHTML exists, non-empty, and parses as XML
- Legacy ZIP member name encodings (utf-8 → cp437 → latin-1)

`--validate-epubs-deep` adds Calibre `ebook-meta` (must open the file). Does **not** scan JS, zip bombs, or non-spine assets.

`collect_epub_paths_for_validation()` walks indexed on-disk paths plus unindexed `.epub` files under scan roots. Indexed books with `epub_validated` / `epub_deep_validated` set are skipped on later runs (migration 009). Flags are cleared when the file path, size, mtime, or content hash changes. `audit_epub_integrity()` reports progress every 1000 valid EPUBs, marks validation flags immediately after each good indexed book, and removes invalid EPUBs as they are found when a removal context is supplied.

Recommended manual schedule on large libraries: `audit --validate-epubs-only` → dry-run `run --validate-epubs-only` → `run --execute --validate-epubs-only`. Not part of default cron (too slow for nightly runs on ~500K books).

### Web UI hardening (untrusted metadata)

- **Descriptions:** rendered with `esc()` as plain text (`white-space: pre-wrap`); not interpreted as HTML
- **Login/register redirects:** `safe_post_login_redirect()` allows only `index.py` or `book.py?id=<digits>` with optional browse query params; rejects CRLF and absolute URLs
- **Fetch metadata:** `merge_fetched_metadata()` fills empty fields by default; overwriting existing values requires the **Overwrite existing metadata** checkbox; null online values never clear stored fields
- **Book detail prev/next:** `neighbor_book_ids()` uses keyset queries on sort key + `books.id` instead of a full-library window sort

Keeper rule: `max(path, key=(len(basename), len(fullpath), path))`.

---

## Session 5 — Library cleanup and favorites (June 2026)

- **`cleanup_library.py` / `exlibris cleanup`:** audit + run; dedupe, index, backfill, prune, force-clean
- **`exlibris/cleanup.py`**, **`exlibris/book_paths.py`:** reconciliation helpers; `scan_single_file()` in scanner
- **`manage_users.py`:** list/delete accounts (stdlib path resolution)
- **Cron:** `scan-library.sh` runs cleanup after each scan
- **Scanner:** repoints when duplicate path wins longest-basename rule (missing or shorter canonical); deletes old shorter file under scan roots
- **Library cards:** small ★ for favorited books when signed in

---

## Session 4 — Navigation, admins, polish (June 2026)

- **`admins.txt`:** file-based admin list (`exlibris/admins.py`); gitignored with `admins.txt.example` in repo
- **Admin curation:** edit title/author/genre; fetch metadata; restore cover (UI + server-side checks)
- **Detail navigation:** `←`/`→` and swipe between books in current library sort/filter context
- **POST context:** `lib_*` hidden fields preserve browse order through edit, fetch, restore, and favorite actions
- **Pages sort:** `page_count` in sort dropdown
- **`detail.js` / `swipe-nav.js`:** keyboard and touch navigation on detail pages
- **CGI path:** `admins.py` resolves paths without importing `exlibris.config` (no pydantic on web UI)

---

## Session 3 — Accounts, curation, FTS (June 2026)

- **User accounts:** `005_users.sql`; scrypt passwords; login/logout/register CGI; `exlibris user create`
- **Favorites:** per-user `user_favorites`; checkbox on detail page; **Favorites only** library filter (login required)
- **Cover curation:** reject placeholder images; restore cover from file; manual metadata edit
- **Scan:** per-file progress; skip unchanged files before Calibre; mark books missing when absent from scanned paths (metadata retained)
- **FTS search:** `006_fts_extend.sql`; `exlibris/cgi/search.py`; library UI uses `books_fts MATCH`
- **Toolbar:** flex layout fix; narrower language dropdown
- **Scripts:** executable bits on scan entry points and CGI scripts in git

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

- **UI:** CGI + `library.js`, `detail.js`, `swipe-nav.js`
- **Books:** default scan `/media/books`; runtime data in `data/`
- **Covers:** sharded `data/covers/NN/{id}.jpg`; static Apache alias; `scripts/shard-covers.py` migrates flat layouts
- **Dedup:** SHA-1 `content_hash`; scanner/cleanup keep longest basename and delete shorter copies; missing files hidden from browse
- **Library browse:** FTS search, pagination, favorites filter (★ on cards when signed in), random sort, keyboard and swipe navigation
- **Detail browse:** prev/next book in current filter/sort context (keyboard + swipe)
- **Accounts:** optional login for favorites; `manage_users.py` for server-side list/delete; open web registration
- **Admins:** `admins.txt` (local, gitignored) gates metadata edit, fetch metadata, restore cover
- **Cleanup:** `cleanup_library.py` / `exlibris cleanup`; cron runs after scan
- **Fetch metadata:** DB + covers only; placeholders rejected; existing cover kept when no new image
- **Apache:** path mount `/exlibris/`; `EXLIBRIS_ROOT` must match server install path
- **Scale target:** ~600K books — FTS + pagination in place; keyset pagination still open

---

## Possible Follow-ups

- Genre/language links from book detail back to filtered library
- Scan status in header (`data/scan.log` or DB)
- Faceted filter counts (e.g. language with book counts)
- Keyset pagination for deep pages
- Optional disable open registration
- Narrow title search (drop `file_name` from title filter)
- Extend admin edit to publisher and series
- Populate `page_count` on scan from Calibre metadata
- `--purge-out-of-root` cleanup flag for DB paths outside scan trees

---

## Conversation Reference

Cursor agent session, June 2026 — initial build (Session 1) and server deployment / UI iteration (Session 2).

Transcript: `44633832-6442-49a7-aa75-ffee74ac2a80`
