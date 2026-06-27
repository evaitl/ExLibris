from pathlib import Path

import typer

from exlibris.config import load_settings, resolve_covers_dir, resolve_database_path
from exlibris.database import get_engine, init_db
from exlibris.scanner import scan_paths

app = typer.Typer(
    no_args_is_help=True,
    help="ExLibris — scan ebook directories and browse your library.",
)


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
        )

    typer.echo(
        f"Scanned {stats.scanned} files, updated {stats.added_or_updated} records, "
        f"skipped {stats.skipped} duplicates."
    )
    if stats.errors:
        typer.echo(f"{len(stats.errors)} issue(s):")
        for err in stats.errors:
            typer.echo(f"  - {err}")


if __name__ == "__main__":
    app()
