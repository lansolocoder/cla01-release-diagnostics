"""Command-line entry point."""

import argparse
import json
import os
import plistlib
import struct
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__

INFO_PLIST_PATH = "Contents/Info.plist"

# Mach-O cpu types (<mach-o/loader.h>).
_CPU_TYPE_X86 = 7
_CPU_TYPE_X86_64 = 0x01000007
_CPU_TYPE_ARM = 12
_CPU_TYPE_ARM64 = 0x0100000C

_MH_MAGIC_64 = 0xFEEDFACF
_MH_MAGIC = 0xFEEDFACE
_MH_CIGAM_64 = 0xCFFAEDFE
_MH_CIGAM = 0xCEFAEDFE
_FAT_MAGIC = 0xCAFEBABE
_FAT_CIGAM = 0xBEBAFECA

# Thin magic -> (header byte order, recognised cputype -> architecture name).
# The swapped (CIGAM) magics carry little-endian header fields.
_THIN_MACHO_MAGICS = {
    _MH_MAGIC_64: (">", {_CPU_TYPE_X86_64: "x86_64"}),
    _MH_MAGIC: (">", {_CPU_TYPE_X86: "i386"}),
    _MH_CIGAM_64: ("<", {_CPU_TYPE_ARM64: "arm64"}),
    _MH_CIGAM: ("<", {_CPU_TYPE_ARM: "armv7"}),
}


class AppInfoError(Exception):
    """Diagnostic failure for an ``app-info`` invocation."""


class ProfileError(Exception):
    """Diagnostic failure for a malformed compatibility profile."""


def _is_version_string(value: object) -> bool:
    """True for a dot-separated decimal version with no leading-zero parts."""
    if not isinstance(value, str):
        return False
    parts = value.split(".")
    for part in parts:
        if not part or not all("0" <= ch <= "9" for ch in part):
            return False
        if len(part) > 1 and part[0] == "0":
            return False
    return True


def _parse_version(value: object) -> list[int] | None:
    if not _is_version_string(value):
        return None
    return [int(part) for part in value.split(".")]


def _compare_versions(left: list[int], right: list[int]) -> int:
    """Compare dotted versions; equal shared parts means more parts is greater."""
    for left_part, right_part in zip(left, right):
        if left_part != right_part:
            return -1 if left_part < right_part else 1
    if len(left) == len(right):
        return 0
    return -1 if len(left) < len(right) else 1


def _inspect_bundle(app_arg: str) -> dict:
    """Validate the ``.app`` layout and gather bundle metadata.

    Returns a dict with ``components``, ``bundle_id``, ``executable`` and
    ``plist`` (the parsed Info.plist object, or ``None`` when absent).
    """
    app_path = app_arg  # keep the user-supplied spelling in diagnostics
    app = Path(app_path)

    if not app.exists():
        raise AppInfoError(f"{app_path}: path does not exist")
    if not app.is_dir():
        raise AppInfoError(f"{app_path}: not a directory")
    if not app.name.endswith(".app"):
        raise AppInfoError(f"{app_path}: bundle name must end with .app")

    contents = app / "Contents"
    if not contents.is_dir():
        raise AppInfoError(f"{app_path}: missing Contents directory")

    components = sorted({"Contents/" + name for name in os.listdir(contents)})

    plist_file = contents / "Info.plist"
    plist_data: dict | None = None
    if plist_file.exists():
        try:
            loaded = plistlib.loads(plist_file.read_bytes())
        except Exception as exc:
            raise AppInfoError(
                f"{plist_file}: invalid property list ({exc})"
            ) from exc
        if not isinstance(loaded, dict):
            raise AppInfoError(
                f"{plist_file}: property list root object is not a dictionary"
            )
        plist_data = loaded

    bundle_id: str | None = None
    executable: str | None = None
    if plist_data is not None:
        identifier = plist_data.get("CFBundleIdentifier")
        bundle_executable = plist_data.get("CFBundleExecutable")
        bundle_id = identifier if isinstance(identifier, str) else None
        executable = (
            bundle_executable if isinstance(bundle_executable, str) else None
        )

    return {
        "components": components,
        "bundle_id": bundle_id,
        "executable": executable,
        "plist": plist_data,
    }


def _collect_app_info(app_arg: str) -> dict:
    """Inspect an ``.app`` bundle and return the JSON-serialisable report."""
    bundle = _inspect_bundle(app_arg)
    status = "ok" if bundle["plist"] is not None else "missing-info-plist"
    return {
        "bundle_id": bundle["bundle_id"],
        "executable": bundle["executable"],
        "info_plist": INFO_PLIST_PATH,
        "components": bundle["components"],
        "status": status,
    }


def _load_profile(profile_arg: str) -> tuple[list[int], str, str]:
    """Validate the profile file and return (target version, cpu, raw version)."""
    profile_path = profile_arg  # keep the user-supplied spelling in diagnostics
    try:
        raw = Path(profile_path).read_bytes()
    except OSError as exc:
        raise ProfileError(f"{profile_path}: cannot read profile ({exc})") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProfileError(
            f"{profile_path}: profile is not valid UTF-8 JSON ({exc})"
        ) from exc
    try:
        profile = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProfileError(f"{profile_path}: invalid JSON profile ({exc})") from exc
    if not isinstance(profile, dict):
        raise ProfileError(f"{profile_path}: profile root must be a JSON object")
    if set(profile.keys()) != {"cpu", "os_version"}:
        raise ProfileError(
            f"{profile_path}: profile must contain only 'os_version' and 'cpu'"
        )

    os_version = profile["os_version"]
    target_version = _parse_version(os_version)
    if target_version is None:
        raise ProfileError(
            f"{profile_path}: os_version must be a dot-separated decimal version "
            "without leading zeros"
        )

    cpu = profile["cpu"]
    if cpu not in ("arm64", "x86_64"):
        raise ProfileError(
            f"{profile_path}: cpu must be 'arm64' or 'x86_64'"
        )

    return target_version, cpu, os_version


def _read_thin_architectures(executable_file: Path) -> list[str]:
    """Identify architectures from a thin Mach-O header's magic and cputype."""
    if not executable_file.is_file():
        return []
    try:
        with executable_file.open("rb") as handle:
            header = handle.read(8)
    except OSError:
        return []
    if len(header) < 8:
        return []

    magic = int.from_bytes(header[:4], "big")
    if magic in (_FAT_MAGIC, _FAT_CIGAM):
        return []
    magic_spec = _THIN_MACHO_MAGICS.get(magic)
    if magic_spec is None:
        return []

    byte_order, cpu_types = magic_spec
    (cputype,) = struct.unpack(byte_order + "I", header[4:8])
    architecture = cpu_types.get(cputype)
    return [architecture] if architecture is not None else []


def _collect_compat_report(app_arg: str, profile_arg: str) -> dict:
    """Inspect bundle and profile and return the JSON-serialisable report."""
    # The profile is validated before the bundle; both failing reports only it.
    target_version, target_cpu, target_version_text = _load_profile(profile_arg)
    bundle = _inspect_bundle(app_arg)

    plist = bundle["plist"] or {}
    required_value = plist.get("LSMinimumSystemVersion")
    required_version = _parse_version(required_value)
    required_os_version = (
        required_value if required_version is not None else None
    )

    architectures: list[str] = []
    executable = bundle["executable"]
    if executable is not None:
        executable_file = Path(app_arg) / "Contents" / "MacOS" / executable
        architectures = sorted(set(_read_thin_architectures(executable_file)))

    blocks: list[dict] = []
    if (
        required_version is not None
        and _compare_versions(required_version, target_version) > 0
    ):
        blocks.append(
            {
                "code": "os-version",
                "detail": {
                    "required": required_os_version,
                    "target": target_version_text,
                },
            }
        )
    if architectures and target_cpu not in architectures:
        blocks.append(
            {
                "code": "cpu",
                "detail": {
                    "supported": architectures,
                    "target": target_cpu,
                },
            }
        )
    blocks.sort(key=lambda block: block["code"])

    return {
        "bundle_id": bundle["bundle_id"],
        "executable": executable,
        "required_os_version": required_os_version,
        "architectures": architectures,
        "blocks": blocks,
        "status": "blocked" if blocks else "compatible",
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
        help="Check a .app bundle against a target-machine profile.",
    )
    compat_parser.add_argument("app", help="path to the .app bundle directory")
    compat_parser.add_argument("profile", help="path to the UTF-8 JSON profile")

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
            report = _collect_compat_report(args.app, args.profile)
        except (ProfileError, AppInfoError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(report))
        return 0

    return 0
