"""Minimal stdlib HTTP server for the ExLibris CGI web UI."""

from __future__ import annotations

import copy
import os
import select
import subprocess
import sys
import urllib.parse
from http import HTTPStatus
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


def parse_cgi_response(
    stdout: bytes,
) -> tuple[int, str, list[tuple[str, str]], bytes]:
    """Parse CGI stdout into HTTP status, headers, and body.

    Honors the CGI ``Status`` header (unlike Python's CGIHTTPRequestHandler,
    which always replies 200 and leaves ``Status`` as a raw header).
    """
    header_blob, sep, body = stdout.partition(b"\r\n\r\n")
    if not sep:
        header_blob, sep, body = stdout.partition(b"\n\n")
    if not sep:
        return 200, "OK", [("Content-Type", "text/plain")], stdout

    status_code = 200
    reason = "OK"
    headers: list[tuple[str, str]] = []
    for raw_line in header_blob.splitlines():
        line = raw_line.decode("iso-8859-1", errors="replace").strip("\r")
        if not line or ":" not in line:
            continue
        name, value = line.split(":", 1)
        name = name.strip()
        value = value.strip()
        if name.lower() == "status":
            parts = value.split(None, 1)
            try:
                status_code = int(parts[0])
            except (TypeError, ValueError, IndexError):
                continue
            reason = parts[1] if len(parts) > 1 else ""
            if not reason:
                try:
                    reason = HTTPStatus(status_code).phrase
                except ValueError:
                    reason = "CGI Status"
            continue
        headers.append((name, value))
    return status_code, reason, headers, body


class ExLibrisRequestHandler(CGIHTTPRequestHandler):
    """CGI + static files under web/, covers from the configured covers dir."""

    cgi_directories = ["/cgi-bin"]
    # Force the subprocess path; we override run_cgi anyway.
    have_fork = False

    def __init__(
        self,
        *args: object,
        covers_dir: Path,
        web_dir: Path,
        **kwargs: object,
    ) -> None:
        self.covers_dir = covers_dir
        self.web_dir = web_dir
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

    def run_cgi(self) -> None:
        """Execute CGI and translate the CGI Status header into HTTP status."""
        scriptfile, scriptname, query, env = self._cgi_command_env()
        if scriptfile is None:
            return

        length_header = self.headers.get("content-length")
        try:
            nbytes = int(length_header) if length_header else 0
        except ValueError:
            nbytes = 0

        data: bytes | None = None
        if self.command.lower() == "post" and nbytes > 0:
            data = self.rfile.read(nbytes)
        # Discard any leftover request body bytes.
        try:
            sock = getattr(self.rfile, "_sock", None) or getattr(
                self.connection, "sock", self.connection
            )
            while select.select([sock], [], [], 0)[0]:
                if not sock.recv(1):
                    break
        except (OSError, TypeError, ValueError):
            pass

        cmdline = [scriptfile]
        if self.is_python(scriptfile):
            cmdline = [sys.executable, "-u", scriptfile]
        if query and "=" not in query:
            cmdline.append(query)

        try:
            completed = subprocess.run(
                cmdline,
                input=data,
                capture_output=True,
                env=env,
                cwd=str(self.web_dir),
                check=False,
            )
        except OSError as exc:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, f"CGI exec failed: {exc}")
            return

        if completed.stderr:
            self.log_error("%s", completed.stderr.decode("utf-8", errors="replace"))
        if completed.returncode:
            self.log_error("CGI script exit code %s", completed.returncode)

        status_code, reason, headers, body = parse_cgi_response(completed.stdout)
        self.send_response(status_code, reason)
        has_content_length = False
        for name, value in headers:
            if name.lower() == "content-length":
                has_content_length = True
            self.send_header(name, value)
        if body and not has_content_length:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command.lower() != "head" and body:
            self.wfile.write(body)

    def _cgi_command_env(
        self,
    ) -> tuple[str | None, str, str, dict[str, str]]:
        """Resolve the CGI script path and environment, or send an error."""
        dir_name, rest = self.cgi_info
        path = f"{dir_name}/{rest}"
        i = path.find("/", len(dir_name) + 1)
        while i >= 0:
            nextdir = path[:i]
            nextrest = path[i + 1 :]
            scriptdir = self.translate_path(nextdir)
            if os.path.isdir(scriptdir):
                dir_name, rest = nextdir, nextrest
                i = path.find("/", len(dir_name) + 1)
            else:
                break

        rest, _, query = rest.partition("?")
        i = rest.find("/")
        if i >= 0:
            script, rest = rest[:i], rest[i:]
        else:
            script, rest = rest, ""

        scriptname = f"{dir_name}/{script}"
        scriptfile = self.translate_path(scriptname)
        if not os.path.exists(scriptfile):
            self.send_error(HTTPStatus.NOT_FOUND, f"No such CGI script ({scriptname!r})")
            return None, scriptname, query, {}
        if not os.path.isfile(scriptfile):
            self.send_error(
                HTTPStatus.FORBIDDEN,
                f"CGI script is not a plain file ({scriptname!r})",
            )
            return None, scriptname, query, {}

        env = copy.deepcopy(os.environ)
        env["SERVER_SOFTWARE"] = self.version_string()
        env["SERVER_NAME"] = self.server.server_name
        env["GATEWAY_INTERFACE"] = "CGI/1.1"
        env["SERVER_PROTOCOL"] = self.protocol_version
        env["SERVER_PORT"] = str(self.server.server_port)
        env["REQUEST_METHOD"] = self.command
        uqrest = urllib.parse.unquote(rest)
        env["PATH_INFO"] = uqrest
        env["PATH_TRANSLATED"] = self.translate_path(uqrest)
        env["SCRIPT_NAME"] = scriptname
        env["QUERY_STRING"] = query
        env["REMOTE_ADDR"] = self.client_address[0]
        if self.headers.get("content-type") is None:
            env["CONTENT_TYPE"] = self.headers.get_content_type()
        else:
            env["CONTENT_TYPE"] = self.headers["content-type"]
        length = self.headers.get("content-length")
        if length:
            env["CONTENT_LENGTH"] = length
        referer = self.headers.get("referer")
        if referer:
            env["HTTP_REFERER"] = referer
        accept = self.headers.get_all("accept", ())
        env["HTTP_ACCEPT"] = ",".join(accept)
        ua = self.headers.get("user-agent")
        if ua:
            env["HTTP_USER_AGENT"] = ua
        co = filter(None, self.headers.get_all("cookie", []))
        cookie_str = ", ".join(co)
        if cookie_str:
            env["HTTP_COOKIE"] = cookie_str
        for key in (
            "QUERY_STRING",
            "REMOTE_HOST",
            "CONTENT_LENGTH",
            "HTTP_USER_AGENT",
            "HTTP_COOKIE",
            "HTTP_REFERER",
        ):
            env.setdefault(key, "")
        return scriptfile, scriptname, query, env

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
