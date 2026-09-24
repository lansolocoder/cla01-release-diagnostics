"""Command-line entry point."""

import argparse
import json
import plistlib
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__


def _component_names(directory: Path, suffix: str) -> list[str]:
    if not directory.is_dir():
        return []
    names = {
        entry.name[: -len(suffix)]
        for entry in directory.iterdir()
        if entry.is_dir() and entry.name.endswith(suffix)
    }
    return sorted(names)


def _read_info_plist(plist_path: Path) -> dict:
    try:
        with plist_path.open("rb") as handle:
            data = plistlib.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _inspect_app(app_path: str) -> int:
    path = Path(app_path)
    error = None
    if not path.is_dir() or not (path / "Contents").is_dir():
        error = "invalid-bundle"
    elif not path.name.endswith(".app"):
        error = "not-app-bundle"
    if error is not None:
        print(json.dumps({"error": error}), file=sys.stderr)
        return 2

    contents = path / "Contents"
    info = _read_info_plist(contents / "Info.plist")
    frameworks_dir = contents / "Frameworks"
    result = {
        "appPath": app_path,
        "bundleIdentifier": _string_or_none(info.get("CFBundleIdentifier")),
        "executableName": _string_or_none(info.get("CFBundleExecutable")),
        "frameworks": _component_names(frameworks_dir, ".framework"),
        "plugins": _component_names(contents / "PlugIns", ".plugin"),
        "nestedApps": _component_names(frameworks_dir, ".app"),
    }
    print(json.dumps(result))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release-workbench",
        description="Local macOS app release and compatibility diagnostics.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    inspect_parser = subparsers.add_parser(
        "inspect-app",
        help="inventory the components of a single .app release bundle",
    )
    inspect_parser.add_argument(
        "app_path",
        metavar="APP_PATH",
        help="path to the .app bundle directory to inspect",
    )
    args = parser.parse_args(argv)
    if args.command == "inspect-app":
        return _inspect_app(args.app_path)
    parser.print_help()
    return 0
