from __future__ import annotations

import importlib.util
from pathlib import Path

import typer

from exlibris.config import load_settings, resolve_covers_dir, resolve_database_path
from exlibris.database import get_engine, init_db
from exlibris.scanner import print_scan_progress, scan_paths
from exlibris.users import UserError, register_user

PROJECT_ROOT = Path(__file__).resolve().parents[1]

app = typer.Typer(
    no_args_is_help=True,
    help="ExLibris — scan ebook directories and browse your library.",
)

user_app = typer.Typer(no_args_is_help=True, help="Manage library user accounts.")
cleanup_app = typer.Typer(no_args_is_help=True, help="Reconcile files with the database.")
app.add_typer(user_app, name="user")
app.add_typer(cleanup_app, name="cleanup")


def _cleanup_argv(command: str, **flags: object) -> list[str]:
    argv = [command]
    for key, value in flags.items():
        if value is None or value is False:
            continue
        if value is True:
            argv.append(f"--{key.replace('_', '-')}")
            continue
        if isinstance(value, list):
            for item in value:
                argv.extend([f"--{key.replace('_', '-')}", str(item)])
            continue
        argv.extend([f"--{key.replace('_', '-')}", str(value)])
    return argv


def _run_cleanup(command: str, **flags: object) -> None:
    script = PROJECT_ROOT / "cleanup_library.py"
    spec = importlib.util.spec_from_file_location("cleanup_library", script)
    if spec is None or spec.loader is None:
        typer.echo(f"cleanup script not found: {script}", err=True)
        raise typer.Exit(code=1)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    argv = _cleanup_argv(command, **flags)
    code = module.main(argv)
    raise typer.Exit(code=code)


@app.command()
def scan(
    config: Path | None = typer.Option(
        None, "--config", "-c", help="Path to config.json"
    ),
    path: list[Path] = typer.Option(
        None, "--path", "-p", help="Directory or file to scan (overrides config)"
    ),
    ebook_meta: str | None = typer.Option(
        None, "--ebook-meta", help="Path to Calibre's ebook-meta executable"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress per-file progress"),
) -> None:
    """Scan directories for ebooks and update the library database."""
    from exlibris.job_lock import LibraryJobLockedError, library_job_lock

    settings = load_settings(config)
    scan_targets = [p.expanduser() for p in path] if path else settings.scan_paths
    if not scan_targets:
        typer.echo("No scan paths configured. Set scan_paths in config.json or pass --path.")
        raise typer.Exit(code=1)

    try:
        with library_job_lock(job_name="library scan"):
            engine = get_engine(resolve_database_path(settings.database_path))
            SessionLocal = init_db(engine)

            with SessionLocal() as session:
                stats = scan_paths(
                    session,
                    scan_targets,
                    ebook_meta_cmd=ebook_meta,
                    covers_dir=resolve_covers_dir(settings.covers_dir),
                    verbose=verbose,
                    on_progress=None if quiet else print_scan_progress,
                    validate_epub=True,
                )
    except LibraryJobLockedError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    summary = (
        f"Scanned {stats.scanned} files, updated {stats.added_or_updated} records"
    )
    if stats.skipped:
        summary += f", skipped {stats.skipped} duplicates"
    if stats.unchanged:
        summary += f", skipped {stats.unchanged} unchanged"
    if stats.marked_missing:
        summary += f", marked {stats.marked_missing} missing"
    if stats.invalid_epubs:
        summary += f", skipped {stats.invalid_epubs} invalid EPUB(s)"
    if stats.files_deleted:
        summary += f", deleted {stats.files_deleted} duplicate file(s)"
    typer.echo(f"{summary}.")
    if stats.errors:
        typer.echo(f"{len(stats.errors)} issue(s):")
        for err in stats.errors:
            typer.echo(f"  - {err}")


@cleanup_app.command("audit")
def cleanup_audit(
    config: Path | None = typer.Option(None, "--config", "-c"),
    database: Path | None = typer.Option(None, "--database", "-d"),
    path: list[Path] | None = typer.Option(None, "--path", "-p"),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    force_clean: bool = typer.Option(False, "--force-clean"),
    validate_epubs: bool = typer.Option(False, "--validate-epubs"),
    validate_epubs_only: bool = typer.Option(False, "--validate-epubs-only"),
    validate_epubs_deep: bool = typer.Option(False, "--validate-epubs-deep"),
    ebook_meta: str | None = typer.Option(None, "--ebook-meta"),
) -> None:
    """Report unindexed, duplicate, absent, and orphan items."""
    _run_cleanup(
        "audit",
        config=config,
        database=database,
        path=path,
        quiet=quiet,
        verbose=verbose,
        force_clean=force_clean,
        validate_epubs=validate_epubs,
        validate_epubs_only=validate_epubs_only,
        validate_epubs_deep=validate_epubs_deep,
        ebook_meta=ebook_meta,
    )


@cleanup_app.command("run")
def cleanup_run(
    config: Path | None = typer.Option(None, "--config", "-c"),
    database: Path | None = typer.Option(None, "--database", "-d"),
    path: list[Path] | None = typer.Option(None, "--path", "-p"),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    execute: bool = typer.Option(False, "--execute"),
    force_clean: bool = typer.Option(False, "--force-clean"),
    backfill_hashes: bool = typer.Option(False, "--backfill-hashes"),
    strip_description_html: bool = typer.Option(False, "--strip-description-html"),
    prune_empty_dirs: bool = typer.Option(False, "--prune-empty-dirs"),
    validate_epubs: bool = typer.Option(False, "--validate-epubs"),
    validate_epubs_only: bool = typer.Option(False, "--validate-epubs-only"),
    validate_epubs_deep: bool = typer.Option(False, "--validate-epubs-deep"),
    ebook_meta: str | None = typer.Option(None, "--ebook-meta"),
) -> None:
    """Deduplicate files, index new EPUBs, optionally purge absent rows."""
    _run_cleanup(
        "run",
        config=config,
        database=database,
        path=path,
        quiet=quiet,
        verbose=verbose,
        execute=execute,
        force_clean=force_clean,
        backfill_hashes=backfill_hashes,
        strip_description_html=strip_description_html,
        prune_empty_dirs=prune_empty_dirs,
        validate_epubs=validate_epubs,
        validate_epubs_only=validate_epubs_only,
        validate_epubs_deep=validate_epubs_deep,
        ebook_meta=ebook_meta,
    )


@app.command("serve")
def serve_cmd(
    config: Path | None = typer.Option(
        None, "--config", "-c", help="Path to config.json"
    ),
    host: str | None = typer.Option(
        None, "--host", help="Bind address (default: web_host from config)"
    ),
    port: int | None = typer.Option(
        None, "--port", "-p", help="Bind port (default: web_port from config)"
    ),
) -> None:
    """Serve the CGI web UI with the built-in HTTP server."""
    from exlibris.web_server import serve

    settings = load_settings(config)
    try:
        serve(settings, host=host, port=port)
    except OSError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@user_app.command("create")
def user_create(
    username: str = typer.Argument(help="Login username"),
    password: str = typer.Option(
        ...,
        prompt=True,
        hide_input=True,
        confirmation_prompt=True,
        help="Account password",
    ),
    config: Path | None = typer.Option(
        None, "--config", "-c", help="Path to config.json"
    ),
) -> None:
    """Create a web login account."""
    cleaned = username.strip()
    if not cleaned:
        typer.echo("Username cannot be empty.")
        raise typer.Exit(code=1)

    settings = load_settings(config)
    db_path = resolve_database_path(settings.database_path)
    engine = get_engine(db_path)
    init_db(engine)

    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        register_user(conn, username=cleaned, password=password)
    except UserError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    finally:
        conn.close()

    typer.echo(f"Created user {cleaned!r}.")


if __name__ == "__main__":
    app()
