"""Command-line entry point."""

import argparse
import json
from collections.abc import Sequence

from . import __version__
from .scanner import InvalidAppPackageError, scan_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release-workbench",
        description="Local macOS app release and compatibility diagnostics.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    scan_parser = subparsers.add_parser(
        "scan", help="Scan a .app package and print a JSON report."
    )
    scan_parser.add_argument("app_path", help="Path to the .app package to scan.")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "scan":
        try:
            result = scan_app(args.app_path)
        except InvalidAppPackageError as exc:
            parser.exit(2, f"error: invalid_app_package: {exc}\n")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2
