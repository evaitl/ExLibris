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
```

## Layout

```text
ExLibris/
  data/            ← runtime data (gitignored)
    library.db
    covers/
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
- Computes SHA-1 to skip duplicate files
- Reads metadata with `ebook-meta`
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

- Search by title, author, publisher, genre, and language
- Configurable page size (10, 25, 50, 100, or 200 books per page)
- Debounced search, jump-to-page, and keyboard shortcuts (<kbd>?</kbd> for help)
- Title and author filters use case-insensitive substring search
- Book detail pages with cover, metadata, HTML descriptions, download, and **Fetch metadata online**
- The fetch button shows “Fetching…” while the request is in progress

### CGI environment variables

| Variable | Purpose |
|----------|---------|
| `EXLIBRIS_DATABASE_PATH` | Path to `data/library.db` |
| `EXLIBRIS_CGI_PREFIX` | URL prefix for CGI scripts (e.g. `/exlibris/cgi-bin/`) |
| `EXLIBRIS_STATIC_URL` | URL to the CSS file (e.g. `/exlibris/static/style.css`) |

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

Edit `EXLIBRIS_ROOT` in `apache/exlibris.conf` if needed, then:

```bash
sudo cp apache/exlibris.conf /etc/apache2/conf-available/exlibris.conf
sudo a2enconf exlibris
sudo apache2ctl configtest
sudo systemctl reload apache2
```

Open **http://localhost/exlibris/**.

| Variable | Example value |
|----------|----------------|
| `EXLIBRIS_CGI_PREFIX` | `/exlibris/cgi-bin/` |
| `EXLIBRIS_STATIC_URL` | `/exlibris/static/style.css` |
| `EXLIBRIS_DATABASE_PATH` | `/path/to/ExLibris/data/library.db` |

#### 4. Python environment

CGI scripts use `#!/usr/bin/env python3` and add the project root to `sys.path`. Browsing the library uses only the Python standard library (plus SQLite). **Fetch metadata online** uses the same — no extra pip packages are required for the web UI.

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

## License

Blue Oak Model License 1.0.0 — see [LICENSE.md](LICENSE.md).
