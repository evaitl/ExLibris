"""Minimal stdlib HTTP server for the ExLibris CGI web UI."""

from __future__ import annotations

import os
import sys
from http.server import CGIHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from exlibris.config import (
    PROJECT_ROOT,
    Settings,
    load_settings,
    resolve_covers_dir,
    resolve_database_path,
)

WEB_DIR = PROJECT_ROOT / "web"
DEFAULT_CGI_PREFIX = "/cgi-bin/"
DEFAULT_STATIC_URL = "/static/style.css"
DEFAULT_COVERS_URL = "/covers"


class ExLibrisRequestHandler(CGIHTTPRequestHandler):
    """CGI + static files under web/, covers from the configured covers dir."""

    cgi_directories = ["/cgi-bin"]

    def __init__(
        self,
        *args: object,
        covers_dir: Path,
        web_dir: Path,
        **kwargs: object,
    ) -> None:
        self.covers_dir = covers_dir
        super().__init__(*args, directory=str(web_dir), **kwargs)  # type: ignore[misc]

    def do_GET(self) -> None:
        if self._redirect_root():
            return
        if self._serve_cover():
            return
        super().do_GET()

    def do_HEAD(self) -> None:
        if self._redirect_root():
            return
        if self._serve_cover(head_only=True):
            return
        super().do_HEAD()

    def do_POST(self) -> None:
        if self.is_cgi():
            self.run_cgi()
            return
        self.send_error(501, f"Unsupported method ({self.command})")

    def _redirect_root(self) -> bool:
        path = urlparse(self.path).path
        if path in ("", "/"):
            self.send_response(302)
            self.send_header("Location", f"{DEFAULT_CGI_PREFIX}index.py")
            self.end_headers()
            return True
        return False

    def _serve_cover(self, *, head_only: bool = False) -> bool:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/covers/"):
            return False

        rel = unquote(parsed.path[len("/covers/") :])
        if not rel or ".." in Path(rel).parts:
            self.send_error(404, "Cover not found")
            return True

        covers_root = self.covers_dir.resolve()
        candidate = (covers_root / rel).resolve()
        try:
            candidate.relative_to(covers_root)
        except ValueError:
            self.send_error(404, "Cover not found")
            return True

        if not candidate.is_file():
            self.send_error(404, "Cover not found")
            return True

        try:
            data = candidate.read_bytes()
        except OSError:
            self.send_error(404, "Cover not found")
            return True

        content_type = "image/jpeg"
        suffix = candidate.suffix.lower()
        if suffix == ".png":
            content_type = "image/png"
        elif suffix == ".webp":
            content_type = "image/webp"
        elif suffix == ".gif":
            content_type = "image/gif"

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        if not head_only:
            self.wfile.write(data)
        return True

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))


def apply_cgi_environment(settings: Settings) -> None:
    """Export paths and URL prefixes for CGI child processes."""
    db_path = resolve_database_path(settings.database_path)
    covers_dir = resolve_covers_dir(settings.covers_dir)
    os.environ["EXLIBRIS_DATABASE_PATH"] = str(db_path)
    os.environ["EXLIBRIS_COVERS_DIR"] = str(covers_dir)
    os.environ["EXLIBRIS_CGI_PREFIX"] = DEFAULT_CGI_PREFIX
    os.environ["EXLIBRIS_STATIC_URL"] = DEFAULT_STATIC_URL
    os.environ["EXLIBRIS_COVERS_URL"] = DEFAULT_COVERS_URL


def _make_handler(covers_dir: Path, web_dir: Path) -> type[ExLibrisRequestHandler]:
    class BoundHandler(ExLibrisRequestHandler):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(
                *args,
                covers_dir=covers_dir,
                web_dir=web_dir,
                **kwargs,
            )

    return BoundHandler


def serve(
    settings: Settings | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
) -> None:
    """Start the built-in web server (blocks until interrupted)."""
    settings = settings or load_settings()
    bind_host = host if host is not None else settings.web_host
    bind_port = port if port is not None else settings.web_port
    covers_dir = resolve_covers_dir(settings.covers_dir)

    if not WEB_DIR.is_dir():
        raise FileNotFoundError(f"Web directory not found: {WEB_DIR}")

    apply_cgi_environment(settings)
    os.chdir(WEB_DIR)

    handler = _make_handler(covers_dir, WEB_DIR)
    httpd = ThreadingHTTPServer((bind_host, bind_port), handler)
    url = f"http://{bind_host}:{bind_port}/cgi-bin/index.py"
    print(f"Serving ExLibris at {url}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    finally:
        httpd.server_close()
