"""Command-line entry point."""

import argparse
import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
from collections.abc import Sequence
from xml.parsers.expat import ExpatError

from . import __version__


# Signature data relative to the bundle root; used as the path recorded for
# signature/trust issues.
_CODE_RESOURCES = os.path.join("Contents", "_CodeSignature", "CodeResources")


def _fail(message: str) -> int:
    """Write one error line to stderr and report exit code 2."""
    print(message, file=sys.stderr)
    return 2


def _validate_bundle(app: str, label: str) -> tuple[str, dict] | None:
    """Validate the bundle layout shared by inspect and diagnose.

    Returns the realpath-normalized bundle path and the parsed Info.plist
    dictionary, or None after writing the one-line failure message.
    """
    if not os.path.exists(app):
        _fail(f"{label}: path does not exist: {app}")
        return None
    if not os.path.isdir(app):
        _fail(f"{label}: not a directory: {app}")
        return None

    contents = os.path.join(app, "Contents")
    plist_path = os.path.join(contents, "Info.plist")
    macos_dir = os.path.join(contents, "MacOS")

    if not os.path.isfile(plist_path):
        _fail(f"{label}: missing Info.plist: {plist_path}")
        return None

    try:
        with open(plist_path, "rb") as plist_file:
            info = plistlib.load(plist_file)
    except (plistlib.InvalidFileException, ValueError, OSError, ExpatError):
        _fail(f"{label}: cannot parse Info.plist: {plist_path}")
        return None
    if not isinstance(info, dict):
        _fail(f"{label}: Info.plist top level is not a dictionary: {plist_path}")
        return None

    if not os.path.isdir(macos_dir):
        _fail(f"{label}: missing Contents/MacOS directory: {macos_dir}")
        return None

    return os.path.realpath(app), info


def _run_command(*args: str) -> subprocess.CompletedProcess[str] | None:
    """Run a command, returning None if the executable cannot be launched."""
    try:
        return subprocess.run(
            list(args), capture_output=True, text=True, check=False
        )
    except OSError:
        return None


def _displayed_field(output: str, key: str) -> str | None:
    """Read a ``key=value`` field from codesign -d output, or None."""
    prefix = key + "="
    for line in output.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return None


def _has_sealed_manifest(output: str) -> bool:
    """Whether codesign reports a sealed resource manifest.

    The display reads ``Sealed Resources version=2 ...`` when a manifest seals
    the bundle and ``Sealed Resources=none`` (or an absent line) otherwise.
    """
    for line in output.splitlines():
        if line.startswith("Sealed Resources"):
            remainder = line[len("Sealed Resources") :].strip().lstrip("=").strip()
            return remainder != "none" and remainder != ""
    return False


def _signature_report(app: str) -> dict:
    """Read signature and trust state via codesign/spctl.

    Never raises: when the platform or system commands cannot provide the
    information, the status degrades to ``unsupported``.
    """
    unavailable = {
        "identifier": None,
        "team_identifier": None,
        "signature_status": "unsupported",
        "trusted": None,
        "issues": [],
    }
    if platform.system() != "Darwin" or shutil.which("codesign") is None:
        return unavailable

    display = _run_command("codesign", "-dvvv", app)
    # codesign writes its display output to stderr; a failed launch also means
    # the information is unavailable.
    if display is None:
        return unavailable
    output = (display.stdout or "") + (display.stderr or "")

    identifier = _displayed_field(output, "Identifier")
    if identifier == "":
        identifier = None
    team_identifier = _displayed_field(output, "TeamIdentifier")
    if team_identifier in (None, "", "not set"):
        team_identifier = None

    # No signature identifier, or no sealed resource manifest: the bundle is
    # effectively unsigned even if signature fragments remain on disk.
    if display.returncode != 0 or identifier is None or not _has_sealed_manifest(output):
        status = "unsigned"
    else:
        verification = _run_command("codesign", "--verify", app)
        if verification is None:
            return unavailable
        status = "signed" if verification.returncode == 0 else "damaged"

    if shutil.which("spctl") is None:
        trusted: bool | None = None
    else:
        assessment = _run_command("spctl", "--assess", "--type", "exec", app)
        if assessment is None:
            trusted = None
        else:
            trusted = assessment.returncode == 0

    # At most one issue per signature file: the status problem takes
    # precedence; trust-denied is reported only when the signature verifies
    # but the system does not trust it.
    issues: list[dict[str, str]] = []
    if status == "unsigned":
        issues.append({"code": "signature-missing", "path": _CODE_RESOURCES})
    elif status == "damaged":
        issues.append({"code": "signature-damaged", "path": _CODE_RESOURCES})
    elif status == "signed" and trusted is False:
        issues.append({"code": "trust-denied", "path": _CODE_RESOURCES})

    return {
        "identifier": identifier,
        "team_identifier": team_identifier,
        "signature_status": status,
        "trusted": trusted,
        "issues": issues,
    }


def inspect_bundle(app: str) -> int:
    """Inspect a built .app bundle and print a JSON summary to stdout."""
    validated = _validate_bundle(app, "inspect")
    if validated is None:
        return 2
    bundle_path, info = validated
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
        "bundle_path": bundle_path,
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


def diagnose_bundle(app: str) -> int:
    """Diagnose signature and trust state of a .app bundle as JSON."""
    validated = _validate_bundle(app, "diagnose")
    if validated is None:
        return 2
    bundle_path, _info = validated

    report = {"bundle_path": bundle_path, **_signature_report(app)}
    json.dump(report, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    arguments = list(argv)

    # Dispatch subcommands before argparse so option-like paths are treated as
    # the single argument they are and every usage error stays a one-line stderr
    # message with exit code 2.
    if arguments[:1] in (["inspect"], ["diagnose"]):
        command = arguments[0]
        if len(arguments) != 2:
            return _fail(f"{command}: expected exactly one argument: <app>")
        if command == "inspect":
            return inspect_bundle(arguments[1])
        return diagnose_bundle(arguments[1])

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
