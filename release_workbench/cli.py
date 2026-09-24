"""Command-line entry point."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .bundle import render_bundle_report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release-workbench",
        description="Local macOS app release and compatibility diagnostics.",
        epilog=(
            "example:\n"
            "  python3 -m release_workbench /Applications/Safari.app\n"
            "    Inspect the bundle and print a JSON report (components and\n"
            "    detected issues) to standard output."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "bundle",
        nargs="?",
        metavar="APP_BUNDLE",
        help=(
            "path to a .app bundle directory to inspect; prints one JSON "
            "object to stdout describing its components and issues"
        ),
    )
    args = parser.parse_args(argv)

    if args.bundle is None:
        parser.print_help()
        return 0

    bundle = args.bundle
    path = Path(bundle)
    if not path.exists() or not path.is_dir() or not path.name.endswith(".app"):
        parser.error(
            f"APP_BUNDLE must be an existing directory whose name ends in "
            f".app: {bundle!r}"
        )  # exits with status 2

    print(render_bundle_report(bundle))
    return 0
