"""Command-line entry point."""

import argparse
import json
import os
import plistlib
import re
import stat
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__

INFO_PLIST_PATH = "Contents/Info.plist"

VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)(\.(0|[1-9]\d*))*$")
PROFILE_CPUS = ("arm64", "x86_64")
PROFILE_FIELDS = frozenset({"os_version", "cpu"})

# Thin Mach-O magics mapped to the architecture of their cputype.
MACHO_MAGICS = {
    b"\xfe\xed\xfa\xcf": "x86_64",  # FEEDFACF
    b"\xfe\xed\xfa\xce": "i386",  # FEEDFACE
    b"\xcf\xfa\xed\xfe": "arm64",  # CFFAEDFE
    b"\xce\xfa\xed\xfe": "armv7",  # CEFAEDFE
}


class AppInfoError(Exception):
    """Diagnostic failure for a command invocation."""


def _validate_app(app_arg: str) -> Path:
    """Validate an ``.app`` bundle path and return its ``Contents`` directory."""
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
    return contents


def _load_info_plist(contents: Path) -> dict | None:
    """Parse ``Contents/Info.plist``; return ``None`` when the file is absent."""
    plist_file = contents / "Info.plist"
    if not plist_file.exists():
        return None
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
    return plist_data


def _collect_app_info(app_arg: str) -> dict:
    """Inspect an ``.app`` bundle and return the JSON-serialisable report."""
    contents = _validate_app(app_arg)

    components = sorted({"Contents/" + name for name in os.listdir(contents)})

    plist_data = _load_info_plist(contents)
    bundle_id: str | None = None
    executable: str | None = None
    if plist_data is not None:
        status = "ok"
        identifier = plist_data.get("CFBundleIdentifier")
        bundle_executable = plist_data.get("CFBundleExecutable")
        bundle_id = identifier if isinstance(identifier, str) else None
        executable = (
            bundle_executable if isinstance(bundle_executable, str) else None
        )
    else:
        status = "missing-info-plist"

    return {
        "bundle_id": bundle_id,
        "executable": executable,
        "info_plist": INFO_PLIST_PATH,
        "components": components,
        "status": status,
    }


def _load_profile(profile_arg: str) -> dict:
    """Load and validate a target-Mac profile JSON file."""
    try:
        raw = Path(profile_arg).read_bytes()
    except OSError as exc:
        raise AppInfoError(f"{profile_arg}: cannot read profile ({exc})") from exc
    try:
        profile = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AppInfoError(f"{profile_arg}: invalid JSON profile ({exc})") from exc
    if not isinstance(profile, dict):
        raise AppInfoError(f"{profile_arg}: profile root must be an object")
    extra = sorted(set(profile) - PROFILE_FIELDS)
    if extra:
        raise AppInfoError(
            f"{profile_arg}: unexpected profile fields: {', '.join(extra)}"
        )
    os_version = profile.get("os_version")
    if not isinstance(os_version, str) or not VERSION_PATTERN.match(os_version):
        raise AppInfoError(f"{profile_arg}: invalid os_version")
    cpu = profile.get("cpu")
    if cpu not in PROFILE_CPUS:
        raise AppInfoError(f"{profile_arg}: invalid cpu")
    return {"os_version": os_version, "cpu": cpu}


def _version_greater(left: str, right: str) -> bool:
    """Compare dotted version strings numerically, segment by segment."""
    left_parts = [int(part) for part in left.split(".")]
    right_parts = [int(part) for part in right.split(".")]
    for lhs, rhs in zip(left_parts, right_parts):
        if lhs != rhs:
            return lhs > rhs
    return len(left_parts) > len(right_parts)


def _detect_architectures(contents: Path, executable: str | None) -> list[str]:
    """Return the sorted architectures of the bundle's thin Mach-O executable."""
    if executable is None:
        return []
    macho = contents / "MacOS" / executable
    try:
        if not macho.is_file():
            return []
        with macho.open("rb") as stream:
            magic = stream.read(4)
    except OSError:
        return []
    arch = MACHO_MAGICS.get(magic)
    return [arch] if arch is not None else []


def _collect_compat_report(app_arg: str, profile: dict) -> dict:
    """Build the compatibility report for an ``.app`` against a target profile."""
    contents = _validate_app(app_arg)
    plist_data = _load_info_plist(contents) or {}

    identifier = plist_data.get("CFBundleIdentifier")
    bundle_id = identifier if isinstance(identifier, str) else None
    bundle_executable = plist_data.get("CFBundleExecutable")
    executable = (
        bundle_executable if isinstance(bundle_executable, str) else None
    )

    minimum = plist_data.get("LSMinimumSystemVersion")
    required_os_version = (
        minimum
        if isinstance(minimum, str) and VERSION_PATTERN.match(minimum)
        else None
    )

    architectures = _detect_architectures(contents, executable)

    blocks = []
    if required_os_version is not None and _version_greater(
        required_os_version, profile["os_version"]
    ):
        blocks.append(
            {
                "code": "os-version",
                "detail": {
                    "required": required_os_version,
                    "target": profile["os_version"],
                },
            }
        )
    if architectures and profile["cpu"] not in architectures:
        blocks.append(
            {
                "code": "cpu",
                "detail": {
                    "supported": architectures,
                    "target": profile["cpu"],
                },
            }
        )
    blocks.sort(key=lambda block: block["code"])

    return {
        "bundle_id": bundle_id,
        "executable": executable,
        "required_os_version": required_os_version,
        "architectures": architectures,
        "blocks": blocks,
        "status": "compatible" if not blocks else "blocked",
    }


def _string_value(plist_data: dict, key: str) -> str | None:
    """Return the string value of ``key``; anything else counts as missing."""
    value = plist_data.get(key)
    return value if isinstance(value, str) else None


def _version_value(plist_data: dict, key: str) -> str | None:
    """Return the version-string value of ``key``; invalid syntax counts as missing."""
    value = plist_data.get(key)
    if isinstance(value, str) and VERSION_PATTERN.match(value):
        return value
    return None


# Info.plist keys compared by release-diff, with their value extraction rules.
METADATA_KEYS = {
    "CFBundleIdentifier": _string_value,
    "CFBundleExecutable": _string_value,
    "LSMinimumSystemVersion": _version_value,
}


def _read_regular_file(path: Path) -> bytes | None:
    """Return the bytes of a regular file; ``None`` for anything else/unreadable."""
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        return path.read_bytes()
    except OSError:
        return None


def _collect_release_diff(old_arg: str, new_arg: str) -> dict:
    """Compare two ``.app`` bundles and return the JSON-serialisable diff report."""
    old_contents = _validate_app(old_arg)
    try:
        new_contents = _validate_app(new_arg)
    except AppInfoError as exc:
        raise AppInfoError(f"{exc} (old bundle: {old_arg})") from exc

    old_plist = _load_info_plist(old_contents)
    try:
        new_plist = _load_info_plist(new_contents)
    except AppInfoError as exc:
        raise AppInfoError(f"{exc} (old bundle: {old_arg})") from exc

    changes = []
    if old_plist is not None and new_plist is not None:
        for key in sorted(METADATA_KEYS):
            extractor = METADATA_KEYS[key]
            old_value = extractor(old_plist, key)
            new_value = extractor(new_plist, key)
            if old_value != new_value:
                changes.append(
                    {
                        "kind": "metadata",
                        "path": INFO_PLIST_PATH,
                        "detail": {"key": key, "old": old_value, "new": new_value},
                    }
                )

    old_names = set(os.listdir(old_contents))
    new_names = set(os.listdir(new_contents))
    missing_in_new = sorted("Contents/" + name for name in old_names - new_names)
    added_in_new = sorted("Contents/" + name for name in new_names - old_names)

    for name in sorted(old_names & new_names):
        old_bytes = _read_regular_file(old_contents / name)
        new_bytes = _read_regular_file(new_contents / name)
        if old_bytes is None or new_bytes is None:
            continue
        if old_bytes != new_bytes:
            changes.append(
                {
                    "kind": "modified",
                    "path": "Contents/" + name,
                    "detail": {
                        "old_size": len(old_bytes),
                        "new_size": len(new_bytes),
                    },
                }
            )

    changes.sort(key=lambda change: (change["kind"], change["path"]))
    status = (
        "identical"
        if not (changes or missing_in_new or added_in_new)
        else "changed"
    )
    return {
        "changes": changes,
        "missing_in_new": missing_in_new,
        "added_in_new": added_in_new,
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

    compat_parser = subparsers.add_parser(
        "compat-report",
        help="Check a .app bundle against a target Mac profile and emit a JSON report.",
    )
    compat_parser.add_argument("app", help="path to the .app bundle directory")
    compat_parser.add_argument("profile", help="path to the target profile JSON file")

    diff_parser = subparsers.add_parser(
        "release-diff",
        help="Compare two .app release bundles and emit a JSON diff report.",
    )
    diff_parser.add_argument("old_app", help="path to the previous .app bundle")
    diff_parser.add_argument("new_app", help="path to the new .app bundle")

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

    if args.command == "compat-report":
        try:
            profile = _load_profile(args.profile)
            report = _collect_compat_report(args.app, profile)
        except AppInfoError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(report))
        return 0

    if args.command == "release-diff":
        try:
            report = _collect_release_diff(args.old_app, args.new_app)
        except AppInfoError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(report))
        return 0

    return 0
