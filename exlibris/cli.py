from pathlib import Path

import typer

from exlibris.config import load_settings, resolve_covers_dir, resolve_database_path
from exlibris.database import get_engine, init_db
from exlibris.scanner import print_scan_progress, scan_paths
from exlibris.users import UserError, register_user

app = typer.Typer(
    no_args_is_help=True,
    help="ExLibris — scan ebook directories and browse your library.",
)

user_app = typer.Typer(no_args_is_help=True, help="Manage library user accounts.")
app.add_typer(user_app, name="user")


@app.command()
def scan(
    config: Path | None = typer.Option(
        None, "--config", "-c", help="Path to config.yaml"
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
    settings = load_settings(config)
    scan_targets = [p.expanduser() for p in path] if path else settings.scan_paths
    if not scan_targets:
        typer.echo("No scan paths configured. Set scan_paths in config.yaml or pass --path.")
        raise typer.Exit(code=1)

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
        )

    summary = (
        f"Scanned {stats.scanned} files, updated {stats.added_or_updated} records"
    )
    if stats.skipped:
        summary += f", skipped {stats.skipped} duplicates"
    if stats.unchanged:
        summary += f", skipped {stats.unchanged} unchanged"
    typer.echo(f"{summary}.")
    if stats.errors:
        typer.echo(f"{len(stats.errors)} issue(s):")
        for err in stats.errors:
            typer.echo(f"  - {err}")


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
        None, "--config", "-c", help="Path to config.yaml"
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
