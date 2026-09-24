"""Command-line entry point."""

import argparse
from collections.abc import Sequence

from . import __version__
from .inspector import run_inspect


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release-workbench",
        description="Local macOS app release and compatibility diagnostics.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    inspect_parser = subparsers.add_parser(
        "inspect",
        help="List the components of a .app bundle as a JSON report.",
        description="Recursively scan the Contents of a .app bundle and print "
        "a JSON component manifest to stdout.",
    )
    inspect_parser.add_argument("app_path", metavar="APP", help="path to the .app bundle")
    args = parser.parse_args(argv)
    if args.command == "inspect":
        return run_inspect(args.app_path)
    parser.print_help()
    return 0
