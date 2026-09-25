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
MACOS_DIR = "Contents/MacOS"
DECLARED_ARCHS_KEY = "LSEntry.archs"

# Mach-O magic numbers (canonical, big-endian byte interpretation).
MH_MAGIC = 0xFEEDFACE
MH_MAGIC_64 = 0xFEEDFACF
FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF

# cputype values that name a supported architecture; anything else is "other".
CPU_TYPE_NAMES = {
    0x01000007: "x86_64",
    0x0100000C: "arm64",
}
ALLOWED_DECLARED_ARCHS = {"x86_64", "arm64", "universal"}


class AppInfoError(Exception):
    """Diagnostic failure for a command invocation."""


def _validate_bundle(app_arg: str) -> tuple[str, Path, Path]:
    """Validate the user-supplied .app path shared by every subcommand."""
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

    return app_path, app, contents


def _load_plist_dictionary(plist_file: Path) -> dict:
    """Load an Info.plist file and require a dictionary root object."""
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


def _read_fat_cpu_types(exec_file: Path, data: bytes, magic: int) -> list[int]:
    """Read slice cputypes from a big-endian fat (universal) header."""
    if len(data) < 8:
        raise AppInfoError(f"{exec_file}: truncated Mach-O fat header")
    _, nfat_arch = struct.unpack_from(">II", data, 0)
    if magic == FAT_MAGIC:
        # fat_arch: cputype, cpusubtype, offset, size, align (all uint32).
        record = struct.Struct(">IIIII")
    else:
        # fat_arch_64: cputype, cpusubtype, offset, size, align, reserved.
        record = struct.Struct(">IIQQII")
    cpu_types: list[int] = []
    offset = 8
    for _ in range(nfat_arch):
        try:
            fields = record.unpack_from(data, offset)
        except struct.error as exc:
            raise AppInfoError(
                f"{exec_file}: truncated Mach-O fat arch entries"
            ) from exc
        cpu_types.append(fields[0])
        offset += record.size
    return cpu_types


def _read_macho_cpu_types(exec_file: Path) -> list[int]:
    """Read the cputype of each architecture slice in a Mach-O file."""
    try:
        data = exec_file.read_bytes()
    except OSError as exc:
        raise AppInfoError(f"{exec_file}: cannot read executable ({exc})") from exc

    if len(data) < 4:
        raise AppInfoError(f"{exec_file}: unrecognized Mach-O magic")
    (magic_be,) = struct.unpack_from(">I", data, 0)

    if magic_be in (FAT_MAGIC, FAT_MAGIC_64):
        # Fat headers and their entries are always big-endian.
        return _read_fat_cpu_types(exec_file, data, magic_be)

    if magic_be in (MH_MAGIC, MH_MAGIC_64):
        endian = ">"
    elif struct.unpack_from("<I", data, 0)[0] in (MH_MAGIC, MH_MAGIC_64):
        # Thin headers store the magic in the file's native byte order.
        endian = "<"
    else:
        raise AppInfoError(
            f"{exec_file}: unrecognized Mach-O magic 0x{magic_be:08x}"
        )

    if len(data) < 8:
        raise AppInfoError(f"{exec_file}: truncated Mach-O header")
    (cputype,) = struct.unpack_from(endian + "I", data, 4)
    return [cputype]


def _collect_app_info(app_arg: str) -> dict:
    """Inspect an ``.app`` bundle and return the JSON-serialisable report."""
    app_path, _app, contents = _validate_bundle(app_arg)

    components = sorted({"Contents/" + name for name in os.listdir(contents)})

    plist_file = contents / "Info.plist"
    bundle_id: str | None = None
    executable: str | None = None
    if plist_file.exists():
        plist_data = _load_plist_dictionary(plist_file)
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


def _declared_archs(plist_data: dict, plist_file: Path) -> set[str]:
    """Parse and validate the LSEntry.archs array from the Info.plist."""
    if DECLARED_ARCHS_KEY not in plist_data:
        return set()
    raw = plist_data[DECLARED_ARCHS_KEY]
    if not isinstance(raw, list):
        raise AppInfoError(
            f"{plist_file}: {DECLARED_ARCHS_KEY} must be an array of strings"
        )
    declared: set[str] = set()
    for arch in raw:
        if not isinstance(arch, str) or arch not in ALLOWED_DECLARED_ARCHS:
            raise AppInfoError(
                f"{plist_file}: invalid architecture in {DECLARED_ARCHS_KEY}: "
                f"{arch!r} (allowed: x86_64, arm64, universal)"
            )
        declared.add(arch)
    return declared


def _collect_arch_check(app_arg: str) -> dict:
    """Check the executable's Mach-O architectures against the plist claim."""
    _app_path, _app, contents = _validate_bundle(app_arg)

    plist_file = contents / "Info.plist"
    if not plist_file.exists():
        raise AppInfoError(f"{plist_file}: missing Info.plist")
    plist_data = _load_plist_dictionary(plist_file)

    bundle_executable = plist_data.get("CFBundleExecutable")
    if not isinstance(bundle_executable, str):
        raise AppInfoError(
            f"{plist_file}: CFBundleExecutable is missing or not a string"
        )

    declared = _declared_archs(plist_data, plist_file)

    exec_file = contents / "MacOS" / bundle_executable
    if not exec_file.is_file():
        raise AppInfoError(
            f"{exec_file}: declared executable is missing or not a regular file"
        )

    cpu_types = _read_macho_cpu_types(exec_file)
    actual = {CPU_TYPE_NAMES[c] for c in cpu_types if c in CPU_TYPE_NAMES}

    if "universal" in declared:
        match = len(cpu_types) >= 2
    elif declared:
        match = declared <= actual
    else:
        # No architecture claim means nothing to compare against.
        match = True

    return {
        "executable": f"{MACOS_DIR}/{bundle_executable}",
        "declared_archs": sorted(declared),
        "actual_archs": sorted(actual),
        "match": match,
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

    arch_check_parser = subparsers.add_parser(
        "arch-check",
        help=(
            "Check that the executable's Mach-O architectures match the "
            "LSEntry.archs claim in Info.plist and emit a JSON report."
        ),
    )
    arch_check_parser.add_argument(
        "app", help="path to the .app bundle directory"
    )

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

    if args.command == "arch-check":
        try:
            report = _collect_arch_check(args.app)
        except AppInfoError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(report))
        return 0

    return 0
