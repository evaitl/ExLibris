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
cp config.yaml.example config.yaml
cp admins.txt.example admins.txt   # then add admin usernames
```

## Layout

```text
ExLibris/
  data/            ← runtime data (gitignored)
    library.db
    covers/
  admins.txt       ← local admin usernames (gitignored; copy from admins.txt.example)
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

```bash
source .venv/bin/activate
python scan_books.py
```

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
- Marks books **missing** when their file is absent from a scanned path (metadata kept; hidden from the web UI until the file reappears)
- Reads metadata with `ebook-meta` only for new or changed files
- Saves cover images to `data/covers/`
- Upserts records into `data/library.db`

Large libraries can take a long time. Each book is committed as it is processed, so the web UI updates while a scan is running.

To rebuild from scratch:

```bash
rm -rf data/
./scripts/setup-data-dir.sh
python scan_books.py
```

### Scheduled scans (cron)

To pick up new books automatically, run a daily scan at 4:00 AM. The helper script activates the project venv, runs `exlibris scan`, and appends output to `data/scan.log`:

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

Cron uses your login user. That user needs read access to `/media/books` and write access to `data/` (same as a manual scan). Incremental scans skip files already indexed by SHA-1, so a nightly run is usually quick unless many new books were added.

Check recent scan output:

```bash
tail -f data/scan.log
```

## Run the web UI

ExLibris serves the library through a Python CGI frontend in `web/`.

### Web UI features

- **Full-text search** (FTS5) by title, author, publisher, and genre — fast on large libraries; no rescan needed
- **Search** filters — each word in a field must match (prefix/token search via FTS; falls back to substring `LIKE` if needed)
- **Pagination** with configurable page size (10, 25, 50, 100, or 200)
- **Jump to page** and **sort** by title, author, published date, size, pages, last scanned, or random
- **Sort direction** (↑/↓) to reverse order
- **Debounced search** — filters apply automatically ~1s after you stop typing
- **Keyboard shortcuts** — press <kbd>?</kbd> for help (`/` focus search, `Esc` clear, `←`/`→` change page on the library; `←`/`→` move between books on detail pages)
- **Touch navigation** — swipe left/right on library and detail pages (same as arrow keys)
- **Accounts** — optional login to save **favorites** (browse and download work without an account)
- **Favorites only** filter when signed in; favorite checkbox on book detail pages
- Book detail pages with cover, formatted dates, file name, HTML descriptions, download
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

After upgrading from an older version, run a scan once to apply database migrations (including FTS index rebuild):

```bash
exlibris scan
```

Incremental scans are quick when nothing changed.

### Library cleanup

`cleanup_library.py` reconciles the file tree with the database: deduplicates on-disk copies (keeps the longest filename), indexes new EPUBs, and optionally removes stale database rows.

```bash
./cleanup_library.py audit                         # read-only report
./cleanup_library.py run                           # dry-run: show planned changes
./cleanup_library.py run --execute                 # dedupe files and index new EPUBs
./cleanup_library.py run --execute --force-clean   # also hard-delete rows with no file on disk
```

Use `-p` / `--path` to override scan roots, `-d` for the database path. `--force-clean` requires `--execute`.

Moved files (same SHA-1 as an existing row) are **repointed** in the database — only `file_path`, `file_name`, size, and mtime are updated; Calibre metadata extraction is not run again.

Manage accounts with `./manage_users.py list` and `./manage_users.py delete USERNAME`.

### CGI environment variables

| Variable | Purpose |
|----------|---------|
| `EXLIBRIS_DATABASE_PATH` | Path to `data/library.db` |
| `EXLIBRIS_CGI_PREFIX` | URL prefix for CGI scripts (e.g. `/exlibris/cgi-bin/`) |
| `EXLIBRIS_STATIC_URL` | URL to the CSS file (e.g. `/exlibris/static/style.css`) |
| `EXLIBRIS_COVERS_DIR` | Path to cover images (set automatically in `apache/exlibris.conf`) |
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
sudo a2enmod cgi env
sudo systemctl reload apache2
chmod +x web/cgi-bin/*.py
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

`apache2ctl configtest` should not warn that `ScriptAlias` is overridden by `Alias`. The shipped config lists `ScriptAlias` before `Alias`.

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

`config.yaml` settings:

| Field | Description |
|-------|-------------|
| `scan_paths` | Directories to scan recursively (default: `/media/books`) |
| `database_path` | SQLite database file (default: `data/library.db`) |
| `covers_dir` | Cover images directory (default: `data/covers`) |

Environment variables (prefix `EXLIBRIS_`) override config values, for example `EXLIBRIS_DATABASE_PATH`.

## How it works

1. **Scanner** (`python scan_books.py` or `exlibris scan`) indexes ebooks and writes metadata to SQLite.
2. **Web UI** (`web/cgi-bin/`) reads the database and displays the collection.

Scanning and serving are separate processes, so you can re-index on a schedule without restarting the web server.

See [DEVELOPMENT.md](DEVELOPMENT.md) for implementation history and server deployment notes.

## License

Blue Oak Model License 1.0.0 — see [LICENSE.md](LICENSE.md).
