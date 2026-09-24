"""Command-line entry point."""

import argparse
from collections.abc import Sequence

from . import __version__
from .inspect_bundle import inspect_app


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release-workbench",
        description="Local macOS app release and compatibility diagnostics.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    inspect_parser = subparsers.add_parser(
        "inspect",
        help="list the component inventory of a .app bundle",
        description="Recursively scan Contents of a .app bundle and print a JSON inventory.",
    )
    inspect_parser.add_argument("app_path", metavar="APP", help="path to the .app bundle directory")

    args = parser.parse_args(argv)
    if args.command == "inspect":
        return inspect_app(args.app_path)
    parser.print_help()
    return 0
