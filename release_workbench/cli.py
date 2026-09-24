"""Command-line entry point."""

import argparse
import json
import os
import plistlib
import sys
from collections.abc import Sequence

from . import __version__


def _fail(message: str) -> int:
    """Write one error line to stderr and report exit code 2."""
    print(message, file=sys.stderr)
    return 2


def _string_value(info: dict, key: str) -> str | None:
    value = info.get(key)
    return value if isinstance(value, str) else None


def inspect_app(app_path: str) -> int:
    """Inspect a macOS .app bundle and print a JSON report to stdout."""
    if not os.path.exists(app_path):
        return _fail(f"inspect: path does not exist: {app_path}")
    if not os.path.isdir(app_path):
        return _fail(f"inspect: not a directory: {app_path}")

    info_path = os.path.join(app_path, "Contents", "Info.plist")
    macos_dir = os.path.join(app_path, "Contents", "MacOS")

    if not os.path.isfile(info_path):
        return _fail(f"inspect: missing Contents/Info.plist: {app_path}")

    try:
        with open(info_path, "rb") as plist_file:
            info = plistlib.load(plist_file)
    except (OSError, ValueError) as exc:
        return _fail(f"inspect: cannot parse Info.plist: {exc}")

    if not isinstance(info, dict):
        return _fail("inspect: Info.plist top-level object is not a dictionary")

    if not os.path.isdir(macos_dir):
        return _fail(f"inspect: missing or invalid Contents/MacOS: {app_path}")

    executables: list[str] = []
    for entry in os.scandir(macos_dir):
        if entry.is_file(follow_symlinks=False):
            executables.append(entry.name)
    executables.sort()

    issues: list[dict[str, str]] = []
    executable = _string_value(info, "CFBundleExecutable")
    if executable is not None:
        candidate = os.path.join(macos_dir, executable)
        if not (os.path.isfile(candidate) and os.access(candidate, os.X_OK)):
            issues.append({"code": "executable-missing", "path": executable})
            executable = None

    report = {
        "bundle_path": os.path.realpath(app_path),
        "bundle_identifier": _string_value(info, "CFBundleIdentifier"),
        "bundle_name": _string_value(info, "CFBundleName"),
        "short_version": _string_value(info, "CFBundleShortVersionString"),
        "executable": executable,
        "executables": executables,
        "issues": issues,
    }
    print(json.dumps(report, indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release-workbench",
        description="Local macOS app release and compatibility diagnostics.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command")
    inspect_parser = subparsers.add_parser(
        "inspect",
        help="inspect a .app bundle and print a JSON report",
    )
    inspect_parser.add_argument("app", help="path to the .app bundle")

    args = parser.parse_args(argv)

    if args.command == "inspect":
        return inspect_app(args.app)

    parser.print_help()
    return 0
