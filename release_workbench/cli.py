"""Command-line entry point."""

import argparse
import json
import os
import plistlib
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__

INFO_PLIST_RELPATH = "Contents/Info.plist"


def _fail(path: str, reason: str) -> int:
    """Report a diagnostic error for *path* and return the exit code."""
    print(f"error: {path}: {reason}", file=sys.stderr)
    return 2


def _app_info(app: str) -> int:
    app_path = Path(app)

    if not app_path.exists():
        return _fail(app, "path does not exist")
    if not app_path.is_dir():
        return _fail(app, "not a directory")
    if not app_path.name.endswith(".app"):
        return _fail(app, "bundle name must end with .app")

    contents_path = app_path / "Contents"
    if not contents_path.is_dir():
        return _fail(app, "missing Contents directory")

    try:
        entry_names = os.listdir(contents_path)
    except OSError as exc:
        return _fail(str(contents_path), f"cannot list Contents: {exc}")
    components = sorted({f"Contents/{name}" for name in entry_names})

    plist_path = contents_path / "Info.plist"
    bundle_id: str | None = None
    executable: str | None = None

    if plist_path.exists():
        try:
            with plist_path.open("rb") as plist_file:
                plist_data = plistlib.load(plist_file)
        except (plistlib.InvalidFileException, ValueError, OSError) as exc:
            return _fail(str(plist_path), f"not a valid plist: {exc}")
        if not isinstance(plist_data, dict):
            return _fail(str(plist_path), "plist root object is not a dictionary")

        status = "ok"
        raw_id = plist_data.get("CFBundleIdentifier")
        if isinstance(raw_id, str):
            bundle_id = raw_id
        raw_executable = plist_data.get("CFBundleExecutable")
        if isinstance(raw_executable, str):
            executable = raw_executable
    else:
        status = "missing-info-plist"

    payload = {
        "bundle_id": bundle_id,
        "executable": executable,
        "info_plist": INFO_PLIST_RELPATH,
        "components": components,
        "status": status,
    }
    print(json.dumps(payload))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release-workbench",
        description="Local macOS app release and compatibility diagnostics.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command")
    app_info_parser = subparsers.add_parser(
        "app-info",
        help="Diagnose an .app bundle structure and emit a single-line JSON report.",
    )
    app_info_parser.add_argument("app", help="path to the .app bundle directory")

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "app-info":
        return _app_info(args.app)
    return 2
