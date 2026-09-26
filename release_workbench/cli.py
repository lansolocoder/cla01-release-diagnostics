"""Command-line entry point."""

import argparse
import json
import plistlib
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__


def _inspect_app(bundle_path: str) -> int:
    path = Path(bundle_path)

    if path.suffix != ".app":
        print(json.dumps({"error": "not-app-bundle"}), file=sys.stderr)
        return 2
    if not path.exists() or not path.is_dir() or not (path / "Contents").is_dir():
        print(json.dumps({"error": "invalid-bundle"}), file=sys.stderr)
        return 2

    info_plist = path / "Contents" / "Info.plist"
    bundle_identifier: str | None = None
    executable_name: str | None = None
    try:
        with info_plist.open("rb") as plist_file:
            plist = plistlib.load(plist_file)
        if isinstance(plist, dict):
            raw_identifier = plist.get("CFBundleIdentifier")
            if isinstance(raw_identifier, str) and raw_identifier:
                bundle_identifier = raw_identifier
            raw_executable = plist.get("CFBundleExecutable")
            if isinstance(raw_executable, str) and raw_executable:
                executable_name = raw_executable
    except Exception:
        # Missing or unreadable/corrupt plist: both fields stay null.
        pass

    def list_bundles(directory: Path, suffix: str) -> list[str]:
        if not directory.is_dir():
            return []
        names = {
            entry.name[: -len(suffix)]
            for entry in directory.iterdir()
            if entry.is_dir() and entry.name.endswith(suffix)
        }
        return sorted(names)

    frameworks_dir = path / "Contents" / "Frameworks"
    plugins_dir = path / "Contents" / "PlugIns"
    inventory = {
        "appPath": bundle_path,
        "bundleIdentifier": bundle_identifier,
        "executableName": executable_name,
        "frameworks": list_bundles(frameworks_dir, ".framework"),
        "plugins": list_bundles(plugins_dir, ".plugin"),
        "nestedApps": list_bundles(frameworks_dir, ".app"),
    }
    print(json.dumps(inventory, ensure_ascii=False))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release-workbench",
        description="Local macOS app release and compatibility diagnostics.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="command")

    inspect_parser = subparsers.add_parser(
        "inspect-app",
        help="Inventory the components of a single .app bundle.",
        description=(
            "Read a .app bundle directory and emit one JSON line describing its "
            "identifier, executable, frameworks, plugins and nested apps."
        ),
    )
    inspect_parser.add_argument("app_path", metavar="APP_PATH", help="path to the .app bundle directory")
    inspect_parser.set_defaults(handler=_inspect_app)

    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.handler(args.app_path)
