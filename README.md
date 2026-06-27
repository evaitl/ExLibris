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

Optional: copy the example config and edit it:

```bash
cp config.yaml.example config.yaml
```

By default, ExLibris scans the `books/` directory in the project root and stores the database in `library.db`.

## Add books

Place your ebook files under `books/` (subdirectories are scanned recursively):

```text
ExLibris/
  books/
    My Book.epub
    Author Name/
      Another Book.epub
```

The `books/` and `covers/` directories are gitignored.

## Scan the library

Scan the default `books/` directory:

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

- Walks each path recursively for supported ebook files
- Reads metadata with `ebook-meta`
- Saves cover images to `covers/`
- Upserts records into `library.db`

Large libraries can take a long time. Each book is committed as it is processed, so the web UI updates while a scan is running.

To rebuild from scratch:

```bash
rm -f library.db
rm -rf covers
python scan_books.py
```

## Run the web UI

ExLibris serves the library through a Python CGI frontend in `web/`.

### Web UI features

- Filter by title, author, publisher, genre, and language
- Title and author filters use case-insensitive substring search
- Book detail pages with cover, metadata, HTML descriptions, and a download button

### CGI environment variables

| Variable | Purpose |
|----------|---------|
| `EXLIBRIS_DATABASE_PATH` | Path to `library.db` |
| `EXLIBRIS_CGI_PREFIX` | URL prefix for CGI scripts (e.g. `/cgi-bin/`) |
| `EXLIBRIS_STATIC_URL` | URL to the CSS file (e.g. `/static/style.css`) |

Downloads are served only from files under the project's `books/` directory.

### Development server (quick test)

For local testing without Apache, use Python's built-in CGI server:

```bash
cd web
EXLIBRIS_CGI_PREFIX=/cgi-bin/ \
EXLIBRIS_STATIC_URL=/static/style.css \
EXLIBRIS_DATABASE_PATH="$(pwd)/../library.db" \
python3 -m http.server --cgi 8080 --bind 127.0.0.1
```

Open [http://127.0.0.1:8080/cgi-bin/index.py](http://127.0.0.1:8080/cgi-bin/index.py).

### Apache

ExLibris is designed to run as CGI under Apache. The `web/` directory is the document root; `web/cgi-bin/` contains the Python entry points.

#### 1. Install and enable Apache

On Debian/Ubuntu:

```bash
sudo apt install apache2
sudo a2enmod cgi env
sudo systemctl reload apache2
```

#### 2. Make CGI scripts executable

```bash
chmod +x web/cgi-bin/*.py
```

#### 3. Create an Apache site configuration

Create `/etc/apache2/sites-available/exlibris.conf` (adjust paths to match your installation):

```apache
<VirtualHost *:80>
    ServerName exlibris.example.com

    # Path to the ExLibris web/ directory
    DocumentRoot /home/evaitl/programming/ExLibris/web

    # Static CSS
    <Directory /home/evaitl/programming/ExLibris/web/static>
        Require all granted
    </Directory>

    # CGI scripts
    ScriptAlias /cgi-bin/ /home/evaitl/programming/ExLibris/web/cgi-bin/

    <Directory /home/evaitl/programming/ExLibris/web/cgi-bin>
        Options +ExecCGI
        SetHandler cgi-script
        Require all granted

        SetEnv EXLIBRIS_DATABASE_PATH /home/evaitl/programming/ExLibris/library.db
        SetEnv EXLIBRIS_CGI_PREFIX /cgi-bin/
        SetEnv EXLIBRIS_STATIC_URL /static/style.css
    </Directory>

    # Open the library at the site root
    RedirectMatch ^/$ /cgi-bin/index.py

    ErrorLog ${APACHE_LOG_DIR}/exlibris-error.log
    CustomLog ${APACHE_LOG_DIR}/exlibris-access.log combined
</VirtualHost>
```

Replace `/home/evaitl/programming/ExLibris` with the absolute path to your clone.

#### 4. Enable the site

```bash
sudo a2ensite exlibris
sudo apache2ctl configtest
sudo systemctl reload apache2
```

Open `http://exlibris.example.com/` (or `http://localhost/` if using the default vhost).

#### 5. File permissions

Apache runs CGI scripts as `www-data`. That user must be able to read:

- `library.db`
- `covers/` (cover images)
- `books/` (ebook downloads)
- the project source (CGI scripts import the `exlibris` package from the repo root)

Example:

```bash
# Allow Apache to traverse the project directory
chmod o+x /home/evaitl /home/evaitl/programming /home/evaitl/programming/ExLibris
chmod -R o+rX web/ covers/ books/
chmod o+r library.db
```

Alternatively, add `www-data` to your user group and grant group read access to the project directory.

#### 6. Python environment

CGI scripts use `#!/usr/bin/env python3` and add the project root to `sys.path` automatically. System Python 3.11+ is sufficient for the web UI.

If you prefer the project virtualenv, change the shebang in each file under `web/cgi-bin/`:

```python
#!/home/evaitl/programming/ExLibris/.venv/bin/python3
```

#### HTTPS

For production, put TLS in front of Apache (for example with [Let's Encrypt](https://letsencrypt.org/) and `certbot --apache`) or terminate HTTPS in a reverse proxy.

## Alternative web server (FastAPI)

ExLibris also includes a FastAPI-based viewer:

```bash
source .venv/bin/activate
exlibris serve
```

Open [http://127.0.0.1:8080](http://127.0.0.1:8080). This uses the same `library.db` but a different UI than the CGI frontend.

## Configuration

`config.yaml` settings:

| Field | Description |
|-------|-------------|
| `scan_paths` | Directories to scan recursively (default: `books`) |
| `database_path` | SQLite database file (default: `library.db`) |
| `host` / `port` | Bind address for `exlibris serve` |

Environment variables (prefix `EXLIBRIS_`) override config values, for example `EXLIBRIS_DATABASE_PATH` or `EXLIBRIS_PORT`.

## How it works

1. **Scanner** (`python scan_books.py` or `exlibris scan`) indexes ebooks and writes metadata to SQLite.
2. **Web UI** (`web/cgi-bin/` or `exlibris serve`) reads the database and displays the collection.

Scanning and serving are separate processes, so you can re-index on a schedule without restarting the web server.

## License

Blue Oak Model License 1.0.0 — see [LICENSE.md](LICENSE.md).
