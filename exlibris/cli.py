from pathlib import Path

import typer
import uvicorn

from exlibris.config import load_settings
from exlibris.database import get_engine, init_db
from exlibris.scanner import scan_paths
from exlibris.web.app import create_app

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

    engine = get_engine(settings.database_path)
    SessionLocal = init_db(engine)

    with SessionLocal() as session:
        stats = scan_paths(
            session,
            scan_targets,
            ebook_meta_cmd=ebook_meta,
            covers_dir=settings.covers_dir,
            verbose=verbose,
        )

    typer.echo(
        f"Scanned {stats.scanned} files, updated {stats.added_or_updated} records."
    )
    if stats.errors:
        typer.echo(f"{len(stats.errors)} issue(s):")
        for err in stats.errors:
            typer.echo(f"  - {err}")


@app.command()
def serve(
    config: Path | None = typer.Option(
        None, "--config", "-c", help="Path to config.yaml"
    ),
    host: str | None = typer.Option(None, "--host", help="Bind address"),
    port: int | None = typer.Option(None, "--port", help="Bind port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes"),
) -> None:
    """Start the web server to browse the library database."""
    settings = load_settings(config)
    bind_host = host or settings.host
    bind_port = port or settings.port

    web_app = create_app(settings)
    typer.echo(f"Serving library at http://{bind_host}:{bind_port}")
    uvicorn.run(web_app, host=bind_host, port=bind_port, reload=reload)


if __name__ == "__main__":
    app()
