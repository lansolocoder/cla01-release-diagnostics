"""Command-line entry point."""

import argparse
import json
import os
import plistlib
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__

INFO_PLIST_PATH = "Contents/Info.plist"
COMPARE_KEYS = (
    "CFBundleExecutable",
    "CFBundleIdentifier",
    "CFBundleShortVersionString",
    "CFBundleVersion",
)


class AppInfoError(Exception):
    """Diagnostic failure for a bundle inspection invocation."""


def _inspect_bundle(app_arg: str) -> tuple[set[str], dict | None]:
    """Validate a ``.app`` bundle and return its components and plist.

    The component set holds the ``Contents/`` first-level entries as paths
    relative to the bundle root. The plist mapping is ``None`` when
    ``Contents/Info.plist`` is absent.
    """
    app_path = app_arg  # keep the user-supplied spelling in diagnostics
    app = Path(app_arg)

    if not app.exists():
        raise AppInfoError(f"{app_path}: path does not exist")
    if not app.is_dir():
        raise AppInfoError(f"{app_path}: not a directory")
    if not app.name.endswith(".app"):
        raise AppInfoError(f"{app_path}: bundle name must end with .app")

    contents = app / "Contents"
    if not contents.is_dir():
        raise AppInfoError(f"{app_path}: missing Contents directory")

    components = {"Contents/" + name for name in os.listdir(contents)}

    plist_file = contents / "Info.plist"
    if not plist_file.exists():
        return components, None

    try:
        plist_data = plistlib.loads(plist_file.read_bytes())
    except Exception as exc:
        raise AppInfoError(
            f"{plist_file}: invalid property list ({exc})"
        ) from exc
    if not isinstance(plist_data, dict):
        raise AppInfoError(
            f"{plist_file}: property list root object is not a dictionary"
        )
    return components, plist_data


def _string_value(plist_data: dict | None, key: str) -> str | None:
    """Return the string value for *key*, or ``None`` when missing/non-string."""
    if plist_data is None:
        return None
    value = plist_data.get(key)
    return value if isinstance(value, str) else None


def _collect_app_info(app_arg: str) -> dict:
    """Inspect an ``.app`` bundle and return the JSON-serialisable report."""
    components, plist_data = _inspect_bundle(app_arg)

    if plist_data is None:
        status = "missing-info-plist"
    else:
        status = "ok"

    return {
        "bundle_id": _string_value(plist_data, "CFBundleIdentifier"),
        "executable": _string_value(plist_data, "CFBundleExecutable"),
        "info_plist": INFO_PLIST_PATH,
        "components": sorted(components),
        "status": status,
    }


def _compare_apps(old_arg: str, new_arg: str) -> dict:
    """Compare two ``.app`` bundles and return the JSON-serialisable report."""
    old_components, old_plist = _inspect_bundle(old_arg)
    new_components, new_plist = _inspect_bundle(new_arg)

    added = sorted(new_components - old_components)
    removed = sorted(old_components - new_components)

    changed: list[dict] = []
    conflict = False
    for key in sorted(COMPARE_KEYS):
        old_value = _string_value(old_plist, key)
        new_value = _string_value(new_plist, key)
        if old_value == new_value:
            continue
        if old_value is not None and new_value is not None:
            # Both sides present but unequal: surfaced via status only.
            conflict = True
        else:
            changed.append({"key": key, "old": old_value, "new": new_value})

    if not added and not removed and not changed and not conflict:
        status = "match"
    elif conflict:
        status = "conflict"
    else:
        status = "differs"

    return {
        "old": old_arg,
        "new": new_arg,
        "added": added,
        "removed": removed,
        "changed": changed,
        "status": status,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release-workbench",
        description="Local macOS app release and compatibility diagnostics.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    app_info_parser = subparsers.add_parser(
        "app-info",
        help="Inspect the structure of a .app bundle and emit a JSON report.",
    )
    app_info_parser.add_argument("app", help="path to the .app bundle directory")

    compare_parser = subparsers.add_parser(
        "compare",
        help="Compare the structure and Info.plist values of two .app bundles.",
    )
    compare_parser.add_argument("old_app", metavar="old-app", help="old .app bundle")
    compare_parser.add_argument("new_app", metavar="new-app", help="new .app bundle")

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "app-info":
        try:
            report = _collect_app_info(args.app)
        except AppInfoError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(report))
        return 0

    if args.command == "compare":
        try:
            report = _compare_apps(args.old_app, args.new_app)
        except AppInfoError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(report))
        return 0

    return 0
