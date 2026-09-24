"""Command-line entry point."""

import argparse
import json
import os
import plistlib
import sys
from collections.abc import Sequence
from xml.parsers.expat import ExpatError

from . import __version__


def _fail(message: str) -> int:
    """Write one error line to stderr and report exit code 2."""
    print(message, file=sys.stderr)
    return 2


def inspect_bundle(app: str) -> int:
    """Inspect a built .app bundle and print a JSON summary to stdout."""
    if not os.path.exists(app):
        return _fail(f"inspect: path does not exist: {app}")
    if not os.path.isdir(app):
        return _fail(f"inspect: not a directory: {app}")

    contents = os.path.join(app, "Contents")
    plist_path = os.path.join(contents, "Info.plist")
    macos_dir = os.path.join(contents, "MacOS")

    if not os.path.isfile(plist_path):
        return _fail(f"inspect: missing Info.plist: {plist_path}")

    try:
        with open(plist_path, "rb") as plist_file:
            info = plistlib.load(plist_file)
    except (plistlib.InvalidFileException, ValueError, OSError, ExpatError):
        return _fail(f"inspect: cannot parse Info.plist: {plist_path}")
    if not isinstance(info, dict):
        return _fail(f"inspect: Info.plist top level is not a dictionary: {plist_path}")

    if not os.path.isdir(macos_dir):
        return _fail(f"inspect: missing Contents/MacOS directory: {macos_dir}")

    def string_value(key: str) -> str | None:
        value = info.get(key)
        return value if isinstance(value, str) else None

    executables: list[str] = []
    with os.scandir(macos_dir) as entries:
        for entry in entries:
            if entry.is_file(follow_symlinks=False):
                executables.append(entry.name)
    executables.sort()

    issues: list[dict[str, str]] = []
    executable_name = string_value("CFBundleExecutable")
    executable: str | None = None
    if executable_name is not None:
        candidate = os.path.join(macos_dir, executable_name)
        if (
            os.path.exists(candidate)
            and not os.path.isdir(candidate)
            and os.access(candidate, os.X_OK)
        ):
            executable = executable_name
        else:
            issues.append({"code": "executable-missing", "path": executable_name})

    report = {
        "bundle_path": os.path.realpath(app),
        "bundle_identifier": string_value("CFBundleIdentifier"),
        "bundle_name": string_value("CFBundleName"),
        "short_version": string_value("CFBundleShortVersionString"),
        "executable": executable,
        "executables": executables,
        "issues": issues,
    }
    json.dump(report, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    arguments = list(argv)

    # Dispatch inspect before argparse so option-like paths are treated as the
    # single argument they are and every usage error stays a one-line stderr
    # message with exit code 2.
    if arguments[:1] == ["inspect"]:
        if len(arguments) != 2:
            return _fail("inspect: expected exactly one argument: <app>")
        return inspect_bundle(arguments[1])

    parser = argparse.ArgumentParser(
        prog="release-workbench",
        description="Local macOS app release and compatibility diagnostics.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="inspect a built .app bundle and print its structure as JSON",
    )
    inspect_parser.add_argument("app", metavar="<app>")

    parser.parse_args(arguments)
    parser.print_help()
    return 0
