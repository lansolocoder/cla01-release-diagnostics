"""Command-line entry point."""

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
from collections.abc import Sequence
from xml.parsers.expat import ExpatError

from . import __version__

SIGNATURE_RELATIVE_PATH = "Contents/_CodeSignature/CodeResources"


def _fail(message: str) -> int:
    """Write one error line to stderr and report exit code 2."""
    print(message, file=sys.stderr)
    return 2


def _read_bundle_info(app: str, command: str) -> tuple[dict | None, int]:
    """Validate the bundle layout and parse its Info.plist.

    Returns ``(info, 0)`` on success; otherwise ``(None, exit_code)`` after
    writing a one-line error to stderr.
    """
    if not os.path.exists(app):
        return None, _fail(f"{command}: path does not exist: {app}")
    if not os.path.isdir(app):
        return None, _fail(f"{command}: not a directory: {app}")

    contents = os.path.join(app, "Contents")
    plist_path = os.path.join(contents, "Info.plist")
    macos_dir = os.path.join(contents, "MacOS")

    if not os.path.isfile(plist_path):
        return None, _fail(f"{command}: missing Info.plist: {plist_path}")

    try:
        with open(plist_path, "rb") as plist_file:
            info = plistlib.load(plist_file)
    except (plistlib.InvalidFileException, ValueError, OSError, ExpatError):
        return None, _fail(f"{command}: cannot parse Info.plist: {plist_path}")
    if not isinstance(info, dict):
        return None, _fail(
            f"{command}: Info.plist top level is not a dictionary: {plist_path}"
        )

    if not os.path.isdir(macos_dir):
        return None, _fail(
            f"{command}: missing Contents/MacOS directory: {macos_dir}"
        )

    return info, 0


def inspect_bundle(app: str) -> int:
    """Inspect a built .app bundle and print a JSON summary to stdout."""
    info, status = _read_bundle_info(app, "inspect")
    if info is None:
        return status

    macos_dir = os.path.join(app, "Contents", "MacOS")

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


def _run_quiet(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        check=False,
    )


def _assess_signature(
    app: str, codesign: str, spctl: str
) -> tuple[str, str | None, str | None, bool | None, list[dict[str, str]]]:
    """Read signature and trust state with codesign/spctl (read-only)."""
    query = _run_quiet([codesign, "-dv", "--verbose=4", app])
    values: dict[str, str] = {}
    for line in query.stderr.splitlines():
        for key in ("Identifier", "TeamIdentifier"):
            prefix = key + "="
            if line.startswith(prefix):
                values[key] = line[len(prefix):]
    identifier = values.get("Identifier")
    team_identifier = values.get("TeamIdentifier")

    manifest = os.path.join(app, SIGNATURE_RELATIVE_PATH)
    if identifier is None or not os.path.isfile(manifest):
        return (
            "unsigned",
            identifier,
            team_identifier,
            None,
            [{"code": "signature-missing", "path": SIGNATURE_RELATIVE_PATH}],
        )

    verify = _run_quiet([codesign, "--verify", "--verbose=4", app])
    if verify.returncode != 0:
        return (
            "damaged",
            identifier,
            team_identifier,
            None,
            [{"code": "signature-damaged", "path": SIGNATURE_RELATIVE_PATH}],
        )

    assess = _run_quiet([spctl, "--assess", "--verbose=4", app])
    trusted = assess.returncode == 0
    issues: list[dict[str, str]] = []
    if not trusted:
        issues.append({"code": "trust-denied", "path": SIGNATURE_RELATIVE_PATH})
    return "signed", identifier, team_identifier, trusted, issues


def diagnose_bundle(app: str) -> int:
    """Diagnose signature and trust state of a .app bundle as JSON."""
    info, status = _read_bundle_info(app, "diagnose")
    if info is None:
        return status

    identifier: str | None = None
    team_identifier: str | None = None
    signature_status = "unsupported"
    trusted: bool | None = None
    issues: list[dict[str, str]] = []

    if sys.platform == "darwin":
        codesign = shutil.which("codesign")
        spctl = shutil.which("spctl")
        if codesign is not None and spctl is not None:
            try:
                (
                    signature_status,
                    identifier,
                    team_identifier,
                    trusted,
                    issues,
                ) = _assess_signature(app, codesign, spctl)
            except OSError:
                # The system commands vanished between lookup and exec.
                signature_status = "unsupported"
                identifier = None
                team_identifier = None
                trusted = None
                issues = []

    report = {
        "bundle_path": os.path.realpath(app),
        "identifier": identifier,
        "team_identifier": team_identifier,
        "signature_status": signature_status,
        "trusted": trusted,
        "issues": issues,
    }
    json.dump(report, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    arguments = list(argv)

    # Dispatch inspect/diagnose before argparse so option-like paths are
    # treated as the single argument they are and every usage error stays a
    # one-line stderr message with exit code 2.
    for command, handler in (
        ("inspect", inspect_bundle),
        ("diagnose", diagnose_bundle),
    ):
        if arguments[:1] == [command]:
            if len(arguments) != 2:
                return _fail(f"{command}: expected exactly one argument: <app>")
            return handler(arguments[1])

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

    diagnose_parser = subparsers.add_parser(
        "diagnose",
        help="diagnose signature and trust state of a .app bundle as JSON",
    )
    diagnose_parser.add_argument("app", metavar="<app>")

    parser.parse_args(arguments)
    parser.print_help()
    return 0
