# ExLibris

ExLibris scans a directory tree for ebooks, extracts metadata into a SQLite database, and serves a web UI to browse your collection.

Supported formats: **EPUB**.

## Requirements

- **Python 3.11+**
- **[Calibre](https://calibre-ebook.com/)** — the scanner calls Calibre's `ebook-meta` command to read metadata and extract cover images
- **pip** and **venv** (on Debian/Ubuntu: `python3-venv` and `python3-pip`)

Verify Calibre is installed:

```bash
ebook-meta --version
```

## Installation

Clone or download this repository, then create a virtual environment and install ExLibris:

```bash
cd ExLibris
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

On Debian/Ubuntu, if `venv` or `pip` are missing:

```bash
sudo apt install python3-venv python3-pip calibre
```

Create the runtime data directory:

```bash
./scripts/setup-data-dir.sh
```

Optional: copy the example config and edit it:

```bash
cp config.json.example config.json
cp admins.txt.example admins.txt   # then add admin usernames
```

## Layout

```text
ExLibris/
  data/            ← runtime data (gitignored)
    library.db
    library.lock     ← pidfile for exclusive maintenance jobs
    covers/
    scan.log
  admins.txt       ← local admin usernames (gitignored; copy from admins.txt.example)
  cleanup_library.py   ← reconcile files vs database
  manage_users.py      ← list/delete web accounts
  scan_books.py        ← standalone scan entry point
  update_epubs.py      ← re-encode EPUBs to version 2
  web/cgi-bin/     ← web UI
  exlibris/        ← Python package

/media/books/      ← default location for EPUB files (outside the repo)
```

By default, ExLibris scans `/media/books` and stores the database and cover images under `data/`.

## Add books

Place your ebook files under `/media/books` (subdirectories are scanned recursively):

```text
/media/books/
  My Book.epub
  Author Name/
    Another Book.epub
```

Create the directory if needed: `sudo mkdir -p /media/books`

## Scan the library

The scanner needs the project virtualenv (SQLAlchemy, Typer, etc.). `scan_books.py` re-runs itself with `.venv/bin/python` when you invoke it with system Python.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
./scan_books.py
```

Or activate the venv explicitly:

```bash
source .venv/bin/activate
python scan_books.py
```

`scan_books.py` and `exlibris scan` validate EPUB structure before indexing new or changed files. Invalid EPUBs are logged, deleted from disk (under scan roots), and not added to the database.

Or use the installed CLI:

```bash
exlibris scan
```

Scan specific paths instead of the default:

```bash
python scan_books.py ~/Downloads/ebooks
exlibris scan --path ~/Downloads/ebooks --path ~/Books
```

The scanner:

- Walks each path recursively for `.epub` files
- Shows per-file progress (`[n/total]`) unless `--quiet`
- Skips unchanged files when size and mtime match the database (no file read); otherwise computes SHA-1 to detect duplicates or content changes before calling Calibre
- **Repoints** the database to a duplicate file with a longer basename when the canonical path is missing or shorter (metadata unchanged; no Calibre), and **deletes** the old shorter on-disk copy when it still exists under a scan root
- Marks books **missing** when their file is absent from a scanned path (metadata kept; hidden from the web UI until the file reappears)
- Reads metadata with `ebook-meta` only for new or changed files
- Saves cover images to `data/covers/NN/{id}.jpg` (sharded by `book_id % 100`)
- Upserts records into `data/library.db`

Large libraries can take a long time. Each book is committed as it is processed, so the web UI updates while a scan is running.

To rebuild from scratch:

```bash
rm -rf data/
./scripts/setup-data-dir.sh
python scan_books.py
```

### Scheduled scans (cron)

To pick up new books automatically, run a daily scan at 4:00 AM. The helper script activates the project venv, runs `exlibris scan`, then runs `cleanup_library.py run --execute --backfill-hashes --prune-empty-dirs`, and appends output to `data/scan.log`:

```bash
chmod +x scripts/scan-library.sh
```

Edit your user crontab:

```bash
crontab -e
```

Add one line (replace `/path/to/ExLibris` with your clone location):

```cron
0 4 * * * /path/to/ExLibris/scripts/scan-library.sh
```

Cron uses your login user. That user needs read access to `/media/books` and write access to `data/` (same as a manual scan). Incremental scans skip unchanged files when size and mtime match, so a nightly run is usually quick unless many new books were added. Cleanup after each scan deduplicates on-disk copies, sanitizes filenames, indexes new EPUBs, backfills missing SHA-1 hashes, and prunes empty directories. EPUB validation/removal (`--validate-epubs`) is not in the default cron job — run it manually when you want to purge corrupt files.

`scan-library.sh` acquires an exclusive lock on `data/library.lock` before starting. If another scan, cleanup, or EPUB update is already running, the cron job logs a skip message and exits. Manual runs of `scan_books.py`, `cleanup_library.py run`, and `update_epubs.py` use the same lock file and refuse to start when a job is already in progress.

Check recent scan output:

```bash
tail -f data/scan.log
```

## Run the web UI

ExLibris serves the library through a Python CGI frontend in `web/`.

### Web UI features

- **Full-text search** (FTS5) by title, author, publisher, and genre — fast on large libraries; no rescan needed
- **Search** filters — each word in a field must match (prefix/token search via FTS; falls back to substring `LIKE` if needed)
- **Pagination** with configurable page size (10, 25, 50, 100, or 200); **Previous/Next** use keyset cursors (`after_id` / `before_id`) for fast browsing at any depth; **Jump to page** still uses offset when you need a specific page number
- **Jump to page** and **sort** by title, author, published date, size, pages, last scanned, or random
- **Sort direction** (↑/↓) to reverse order
- **Debounced search** — filters apply automatically ~2s after you stop typing
- **Keyboard shortcuts** — press <kbd>?</kbd> for help (`/` focus search, `Esc` clear, `←`/`→` change page on the library; `←`/`→` move between books on detail pages)
- **Touch navigation** — swipe left/right on library and detail pages (same as arrow keys)
- **Accounts** — optional login to save **favorites** (browse and download work without an account)
- **Favorites only** filter when signed in; favorite checkbox on book detail pages; small star on library cards when signed in
- Book detail pages with cover, formatted dates, file name, plain-text descriptions (HTML escaped), download
- **Edit title, author, and genre** on the detail page for administrators listed in `admins.txt` (stored in the database only; EPUB files are not modified)
- **Fetch metadata online** and **restore cover from file** (embedded EPUB cover) — administrators only
- Fetch updates metadata only; placeholder covers from online sources are rejected; existing covers are kept when no real image is found

### User accounts

Accounts are only required for favorites. Create them from the web (**Create account** in the header) or on the server:

```bash
exlibris user create yourname
```

Passwords are stored as scrypt hashes, never plain text.

**Administrators:** copy the example file and list usernames (one per line). Each name must match a registered account. Admins must be logged in to use curation actions.

```bash
cp admins.txt.example admins.txt
# edit admins.txt — add your username(s)
```

Admin capabilities on the book detail page:

- Edit title, author, and genre (database only; EPUB files unchanged)
- Fetch metadata online
- Restore cover from the embedded EPUB image

**Fetch metadata** fills empty fields by default. To replace existing title, author, publisher, etc., check **Overwrite existing metadata** on the fetch form. Null values from online sources never clear stored fields.

After upgrading from an older version, run a scan once to apply database migrations (including FTS index rebuild):

```bash
exlibris scan
```

Incremental scans are quick when nothing changed.

### Library cleanup

`cleanup_library.py` (or `exlibris cleanup`) reconciles the file tree with the database.

| Action | Command |
|--------|---------|
| Read-only report | `./cleanup_library.py audit` |
| Dry-run | `./cleanup_library.py run` |
| Apply dedupe + index new EPUBs | `./cleanup_library.py run --execute` |
| Also backfill SHA-1, prune empty dirs | add `--backfill-hashes --prune-empty-dirs` |
| Strip HTML from descriptions | add `--strip-description-html` |
| Sanitize filenames + update DB paths | included in `run` (dry-run or `--execute`) |
| Validate EPUB structure / readability | add `--validate-epubs` (optional `--validate-epubs-deep`) |
| Validate EPUBs only (skip dedupe/index) | `--validate-epubs-only` |
| Remove corrupt EPUBs + DB rows | `run --execute --validate-epubs` (destructive; dry-run first) |
| Hard-delete rows with no file on disk | add `--force-clean` (requires `--execute`) |

```bash
./cleanup_library.py audit
./cleanup_library.py audit --validate-epubs-only          # invalid EPUBs only (read-only)
./cleanup_library.py run --validate-epubs-only            # dry-run removal only
./cleanup_library.py run --execute --validate-epubs-only    # remove bad EPUBs; no other cleanup
./cleanup_library.py run --execute --backfill-hashes --prune-empty-dirs
./cleanup_library.py run --execute --strip-description-html   # plain-text descriptions
./cleanup_library.py run --execute --validate-epubs       # full cleanup + remove bad EPUBs
exlibris cleanup run --execute --backfill-hashes --prune-empty-dirs
exlibris cleanup run --execute --strip-description-html
```

Use `-p` / `--path` to override scan roots, `-d` for the database path. `--force-clean` and `--validate-epubs` with `--execute` are destructive (remove files and/or DB rows). Run a dry-run first on large libraries.

**Dedup:** unindexed files with the same SHA-1 as a database row keep the **longest basename**; shorter copies are deleted and the row is repointed. The scanner applies the same rule during `exlibris scan` (repoint + delete old file).

**Moved files:** same SHA-1 at a new path updates only `file_path`, `file_name`, size, and mtime — Calibre is not run again (also handled during `exlibris scan` when the canonical path is missing or shorter).

**New EPUBs:** files on disk with no hash match are indexed via the same logic as `exlibris scan` (needs venv + Calibre).

**Filenames:** `run` renames unsafe characters and very short basenames (stem &lt; 10 characters) to `{title} - {authors}-({publisher}).epub`, then updates `file_path` and `file_name` in the database. `audit` lists planned renames under **Filename fixes**.

**Descriptions:** `--strip-description-html` converts stored book descriptions to plain text: HTML tags are removed, HTML entities are decoded (`&amp;`, `&#39;`, etc.), and whitespace is normalized. Empty results become `NULL`. Updates commit per row so interrupted runs keep progress. The web UI always escapes descriptions when rendering; this flag cleans the database copy (useful after imports from Calibre or online metadata). Dry-run first: `run --strip-description-html`, then `run --execute --strip-description-html`.

**EPUB validation:** `--validate-epubs` checks ZIP integrity (CRC/decompression), `container.xml`, OPF manifest/spine, and parses spine HTML/XHTML. Validates every indexed on-disk book plus unindexed `.epub` files under scan roots. Indexed books that pass are recorded in the database (`epub_validated` / `epub_deep_validated`, migration 009) and skipped on later runs until the file changes. Progress is printed every 1000 valid files. With `run --execute`, invalid files are deleted from disk and indexed rows (plus cover images) are purged. Dry-run lists them under **Invalid EPUBs**. Add `--validate-epubs-deep` to also require Calibre `ebook-meta` to open each file (slower; needs Calibre on `PATH`).

Use `--validate-epubs-only` to skip dedupe, filename sanitization, and indexing — useful for a periodic integrity pass on a large library. EPUB validation is **not** in the default cron job; run it manually after big imports or on a schedule.

**What validation does not do:** it is not antivirus or malware scanning. It does not audit JavaScript, external links, or every file in the archive — only structural/readability checks. Opening an EPUB in a reader is still a separate trust boundary. Book descriptions in the web UI are escaped as plain text so HTML in metadata cannot run scripts in the browser.

**Suggested workflow for mixed/untrusted sources:**

1. `audit --validate-epubs-only` — read-only count of bad files
2. `run --validate-epubs-only` — dry-run what would be deleted
3. `run --execute --validate-epubs-only` — remove corrupt EPUBs and purge DB rows

On very large libraries, run in `screen` or `tmux`; a full pass can take hours.

`audit` uses system Python only; `run --execute` indexes new files via Calibre and re-runs itself with `.venv/bin/python` when started without the venv (same as `scan_books.py`).

### User account maintenance

```bash
./manage_users.py list
./manage_users.py delete USERNAME
```

Works with system Python (no venv required).

### Cover images

Covers are stored under `data/covers/` in shard subdirectories by the **last two digits** of the book id (`book_id % 100`). Example: book `12342` → `data/covers/42/12342.jpg`. The path is stored in `books.cover_path`.

The web UI serves covers as **static files** (Apache `Alias` to `data/covers/`), not via CGI — one less database lookup per thumbnail on the library grid.

**Upgrading an existing library** with flat `data/covers/{id}.jpg` files:

```bash
./scripts/shard-covers.py              # dry-run: report moves and DB updates
./scripts/shard-covers.py --execute    # move files and update books.cover_path
```

No rescan required. New scans and admin cover actions write sharded paths automatically.

Legacy `cover.py?id=…` URLs redirect (301) to the static cover URL.

### CGI environment variables

| Variable | Purpose |
|----------|---------|
| `EXLIBRIS_DATABASE_PATH` | Path to `data/library.db` |
| `EXLIBRIS_CGI_PREFIX` | URL prefix for CGI scripts (e.g. `/exlibris/cgi-bin/`) |
| `EXLIBRIS_STATIC_URL` | URL to the CSS file (e.g. `/exlibris/static/style.css`) |
| `EXLIBRIS_COVERS_URL` | URL prefix for cover images (e.g. `/exlibris/covers`) |
| `EXLIBRIS_COVERS_DIR` | Path to cover images on disk (set automatically in `apache/exlibris.conf`) |
| `EXLIBRIS_SESSION_SECRET` | Optional secret for signed login cookies (recommended in production) |

Downloads are served only from files under configured scan paths (default: `/media/books`). Fetch metadata updates the database and cover images only — EPUB files are not modified.

### Development server (quick test)

```bash
cd web
EXLIBRIS_CGI_PREFIX=/cgi-bin/ \
EXLIBRIS_STATIC_URL=/static/style.css \
EXLIBRIS_DATABASE_PATH="$(pwd)/../data/library.db" \
python3 -m http.server --cgi 8080 --bind 127.0.0.1
```

Open [http://127.0.0.1:8080/cgi-bin/index.py](http://127.0.0.1:8080/cgi-bin/index.py).

### Apache

ExLibris mounts on a **URL path** (for example `/exlibris/`) on the default Apache site.

#### 1. Install and enable Apache

```bash
sudo apt install apache2
sudo a2enmod cgi env headers
sudo systemctl reload apache2
chmod +x web/cgi-bin/*.py scripts/shard-covers.py
```

#### 2. Prepare the data directory

```bash
./scripts/setup-data-dir.sh
```

Follow the script output to grant `www-data` write access to `data/` only (group permissions). Apache needs read access to `/media/books`, `web/`, and `exlibris/`; write access only on `data/`.

#### 3. Add the path-based configuration

Edit `EXLIBRIS_ROOT` in `apache/exlibris.conf` to the **actual install path on the server** (for example `/opt/exlibris` or `/media/books/ExLibris`), then:

```bash
sudo cp apache/exlibris.conf /etc/apache2/conf-available/exlibris.conf
sudo a2enconf exlibris
sudo apache2ctl configtest
sudo systemctl reload apache2
```

`apache2ctl configtest` should not warn that `ScriptAlias` is overridden by `Alias`. The shipped config lists `ScriptAlias` before `Alias`, and adds a `covers/` static alias with cache headers.

After upgrading from a version with flat cover files, run `./scripts/shard-covers.py --execute` once, then reload Apache if you updated `exlibris.conf`.

Open **http://localhost/exlibris/**.

#### 4. Permissions for `www-data`

The Apache user needs read access to the install tree and `/media/books`, and **write access only on `data/`**:

```bash
sudo usermod -aG yourgroup www-data
chmod 2775 data/ data/covers
chmod g+rw data/library.db data/library.db-wal data/library.db-shm
sudo systemctl restart apache2
```

If the install lives under your home directory, `www-data` must be able to traverse each parent directory, or move the install elsewhere.

#### 5. Python environment

CGI scripts use `#!/usr/bin/env python3` and add the project root to `sys.path`. Browsing the library uses only the Python standard library (plus SQLite) — no pip packages are required for the web UI. Admin curation (fetch metadata) needs Calibre's `fetch-ebook-metadata` on `PATH` for the Apache user.

The scanner CLI (`exlibris scan`) still needs the project venv:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Fetch metadata also needs Calibre's `fetch-ebook-metadata` on `PATH` for the Apache user (`www-data`). Quick check:

```bash
sudo -u www-data fetch-ebook-metadata --version
```

If that fails, install Calibre system-wide or symlink the binary into `/usr/local/bin`.

## Configuration

`config.json` settings:

| Field | Description |
|-------|-------------|
| `scan_paths` | Directories to scan recursively (default: `/media/books`) |
| `database_path` | SQLite database file (default: `data/library.db`) |
| `covers_dir` | Cover images directory (default: `data/covers`) |

Copy `config.json.example` to `config.json` and edit as needed. If you still have `config.yaml` from an older install, convert it to JSON (same field names) or rely on environment variables.

Environment variables override config values, for example `EXLIBRIS_DATABASE_PATH` and `EXLIBRIS_SCAN_PATHS` (paths separated with `:` on Linux).

## How it works

1. **Scanner** (`python scan_books.py` or `exlibris scan`) indexes ebooks and writes metadata to SQLite.
2. **Cleanup** (`cleanup_library.py` or `exlibris cleanup`) deduplicates files, indexes orphans, and optionally purges stale rows.
3. **Web UI** (`web/cgi-bin/`) reads the database and displays the collection.

Scanning, cleanup, and serving are separate processes. Cron runs scan then cleanup nightly; the web server does not need a restart.

### EPUB 2 conversion

If some EPUBs fail in readers, re-encode them with Calibre:

```bash
./update_epubs.py                         # dry-run: count candidates
./update_epubs.py --execute               # convert in place, update DB
./update_epubs.py --execute -p ~/books  # limit to one scan root
```

Uses `ebook-convert … --epub-version=2`, replaces each file under its original path, updates `content_hash` / size / mtime, and sets `epub_version2` so interrupted runs skip finished books. After each conversion, structural EPUB validation runs automatically; valid files are marked `epub_validated`. Failed conversions or post-conversion validation failures delete the file and purge the database row (and cover). Requires Calibre on `PATH` and the project venv. Uses the same `data/library.lock` as scan and cleanup — do not run concurrently with those jobs.

See [DEVELOPMENT.md](DEVELOPMENT.md) for implementation history and server deployment notes.

## License

Blue Oak Model License 1.0.0 — see [LICENSE.md](LICENSE.md).
