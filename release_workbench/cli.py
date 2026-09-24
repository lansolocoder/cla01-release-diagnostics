"""Command-line entry point."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .bundle import inspect_bundle


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release-workbench",
        description="Local macOS app release and compatibility diagnostics.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    inspect_parser = subparsers.add_parser(
        "inspect",
        help="Inspect a .app bundle and print a JSON report to stdout.",
        description=(
            "Inspect the component layout of a .app bundle: bundle identifier, "
            "executable, frameworks, plugins, and structural issues. "
            "The report is written to stdout as JSON."
        ),
    )
    inspect_parser.add_argument("bundle", help="Path to the .app bundle directory.")
    args = parser.parse_args(argv)
    if args.command == "inspect":
        return _run_inspect(args.bundle)
    parser.print_help()
    return 0


def _run_inspect(argument: str) -> int:
    bundle = Path(argument)
    if not bundle.is_dir() or bundle.suffix != ".app":
        print(f"error: not an .app bundle directory: {argument}", file=sys.stderr)
        return 2
    report = {"bundle": argument, **inspect_bundle(bundle)}
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0
