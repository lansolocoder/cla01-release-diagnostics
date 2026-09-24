"""Command-line entry point."""

import argparse
import json
import sys
from collections.abc import Sequence

from . import __version__
from .scanner import InvalidAppPackageError, scan_app


def _scan(path: str) -> int:
    try:
        result = scan_app(path)
    except InvalidAppPackageError as exc:
        print(
            f"release-workbench: scan: invalid_app_package: {exc}",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release-workbench",
        description="Local macOS app release and compatibility diagnostics.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    scan_parser = subparsers.add_parser(
        "scan",
        help="scan a built .app bundle and emit a JSON report",
        description="Scan a built .app bundle and emit a one-line JSON report.",
    )
    scan_parser.add_argument("app_path", metavar="app", help="path to the .app bundle")

    args = parser.parse_args(argv)

    if args.command == "scan":
        return _scan(args.app_path)

    parser.print_help()
    return 0
