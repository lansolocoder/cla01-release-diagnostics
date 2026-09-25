"""Command-line entry point."""

import argparse
from collections.abc import Sequence

from . import __version__
from .inspect_bundle import inspect_app
from .verify_signature import verify_signature


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
    verify_parser = subparsers.add_parser(
        "verify-signature",
        help="check embedded code signatures of Mach-O files in a .app bundle",
        description="Scan regular files below Contents of a .app bundle, parse the embedded "
        "code signature of every recognized Mach-O, and print a JSON report.",
    )
    verify_parser.add_argument("app_path", metavar="APP", help="path to the .app bundle directory")
    verify_parser.add_argument(
        "--team-id",
        metavar="TEAM_ID",
        default=None,
        help="expected team identifier; mismatches are reported",
    )

    args = parser.parse_args(argv)
    if args.command == "inspect":
        return inspect_app(args.app_path)
    if args.command == "verify-signature":
        return verify_signature(args.app_path, team_id=args.team_id)
    parser.print_help()
    return 0
