#!/usr/bin/env python3
"""Serve the ExLibris books web UI (stdlib CGI server)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from exlibris.config import load_settings
from exlibris.web_server import serve


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve the ExLibris CGI web UI (host/port from config.json)."
    )
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=None,
        help="Path to config.json",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Bind address (default: web_host from config, or 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=None,
        help="Bind port (default: web_port from config, or 8080)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings(args.config)
    try:
        serve(settings, host=args.host, port=args.port)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
