"""Command-line entry point."""

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
import plistlib
import sys

from . import __version__


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release-workbench",
        description="Local macOS app release and compatibility diagnostics.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    app_info = subparsers.add_parser(
        "app-info",
        help="Run basic checks against a macOS .app bundle.",
    )
    app_info.add_argument("app_path", help="Path to the .app bundle directory.")

    args = parser.parse_args(argv)
    if args.command == "app-info":
        return run_app_info(args.app_path)

    parser.print_help()
    return 0


def run_app_info(app_path: str) -> int:
    """Check an .app bundle and print a JSON summary on success."""
    path = Path(app_path)

    if not path.name.endswith(".app") or not path.is_dir():
        return fail("bundle directory name must end with .app")

    info_plist = path / "Contents" / "Info.plist"
    if not info_plist.is_file():
        return fail("missing Contents/Info.plist")
    try:
        with info_plist.open("rb") as stream:
            plist = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return fail("Contents/Info.plist is not a parseable plist")
    if not isinstance(plist, dict):
        return fail("Contents/Info.plist is not a parseable plist")

    bundle_identifier = plist.get("CFBundleIdentifier")
    if not isinstance(bundle_identifier, str) or not bundle_identifier:
        return fail("CFBundleIdentifier must be a non-empty string")
    short_version = plist.get("CFBundleShortVersionString")
    if not isinstance(short_version, str) or not short_version:
        return fail("CFBundleShortVersionString must be a non-empty string")

    macos_dir = path / "Contents" / "MacOS"
    if not macos_dir.is_dir():
        return fail("missing Contents/MacOS directory")
    executable_count = sum(1 for entry in macos_dir.iterdir() if entry.is_file())
    if executable_count == 0:
        return fail("Contents/MacOS contains no regular file")

    signed = (path / "Contents" / "_CodeSignature" / "CodeResources").exists()

    print(
        json.dumps(
            {
                "bundleIdentifier": bundle_identifier,
                "shortVersion": short_version,
                "executableCount": executable_count,
                "signed": signed,
            }
        )
    )
    return 0


def fail(reason: str) -> int:
    print(f"app-info: {reason}", file=sys.stderr)
    return 2
