"""Command-line entry point."""

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
import plistlib
import sys

from . import __version__


def _fail(reason: str) -> int:
    print(f"app-info: {reason}", file=sys.stderr)
    return 2


def run_app_info(app_path: Path) -> int:
    """Inspect a macOS .app bundle and print a JSON summary."""
    if not app_path.is_dir() or app_path.name[-4:] != ".app":
        return _fail(f"not a .app bundle directory: {app_path}")

    info_plist_path = app_path / "Contents" / "Info.plist"
    if not info_plist_path.is_file():
        return _fail("missing Contents/Info.plist")
    try:
        with info_plist_path.open("rb") as stream:
            info = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return _fail("cannot parse Contents/Info.plist")
    if not isinstance(info, dict):
        return _fail("Contents/Info.plist is not a dictionary")

    bundle_identifier = info.get("CFBundleIdentifier")
    if not isinstance(bundle_identifier, str) or not bundle_identifier:
        return _fail("missing or invalid CFBundleIdentifier")
    short_version = info.get("CFBundleShortVersionString")
    if not isinstance(short_version, str) or not short_version:
        return _fail("missing or invalid CFBundleShortVersionString")

    macos_dir = app_path / "Contents" / "MacOS"
    if not macos_dir.is_dir():
        return _fail("missing Contents/MacOS")
    executables = [entry for entry in macos_dir.iterdir() if entry.is_file()]
    if not executables:
        return _fail("Contents/MacOS contains no files")

    signed = (app_path / "Contents" / "_CodeSignature" / "CodeResources").exists()

    print(
        json.dumps(
            {
                "bundleIdentifier": bundle_identifier,
                "shortVersion": short_version,
                "executableCount": len(executables),
                "signed": signed,
            }
        )
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release-workbench",
        description="Local macOS app release and compatibility diagnostics.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    app_info = subparsers.add_parser(
        "app-info", help="Run basic checks on a macOS .app bundle."
    )
    app_info.add_argument("app_path", metavar="app-path", type=Path)

    args = parser.parse_args(argv)
    if args.command == "app-info":
        return run_app_info(args.app_path)
    parser.print_help()
    return 0
