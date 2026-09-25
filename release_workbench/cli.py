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

VALID_DECLARED_ARCHS = {"arm64", "universal", "x86_64"}

CPU_TYPE_X86_64 = 0x01000007
CPU_TYPE_ARM64 = 0x0100000C

MH_MAGIC = 0xFEEDFACE
MH_MAGIC_64 = 0xFEEDFACF
FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF


class AppInfoError(Exception):
    """Diagnostic failure for a command invocation."""


def _validate_app_bundle(app_arg: str) -> Path:
    """Validate the ``.app`` bundle layout and return its Contents directory."""
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


def _load_info_plist(plist_file: Path) -> dict:
    """Parse ``Info.plist`` and return its root dictionary."""
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
    contents = _validate_app_bundle(app_arg)

    components = sorted({"Contents/" + name for name in os.listdir(contents)})

    plist_file = contents / "Info.plist"
    bundle_id: str | None = None
    executable: str | None = None
    if plist_file.exists():
        plist_data = _load_info_plist(plist_file)
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


def _cputype_name(cputype: int) -> str:
    """Map a Mach-O cputype to a lowercase architecture name."""
    if cputype == CPU_TYPE_X86_64:
        return "x86_64"
    if cputype == CPU_TYPE_ARM64:
        return "arm64"
    return "other"


def _macho_archs(executable_file: Path) -> set[str]:
    """Read the architecture set from a Mach-O executable header."""
    try:
        raw = executable_file.read_bytes()
    except OSError as exc:
        raise AppInfoError(
            f"{executable_file}: cannot read executable ({exc})"
        ) from exc

    if len(raw) < 4:
        raise AppInfoError(f"{executable_file}: not a Mach-O executable")

    magic_le = int.from_bytes(raw[0:4], "little")
    magic_be = int.from_bytes(raw[0:4], "big")

    if magic_le in (MH_MAGIC, MH_MAGIC_64):
        if len(raw) < 8:
            raise AppInfoError(
                f"{executable_file}: truncated Mach-O header"
            )
        cputype = int.from_bytes(raw[4:8], "little")
        return {_cputype_name(cputype)}

    if magic_be in (FAT_MAGIC, FAT_MAGIC_64):
        if len(raw) < 8:
            raise AppInfoError(
                f"{executable_file}: truncated Mach-O fat header"
            )
        nfat_arch = int.from_bytes(raw[4:8], "big")
        entry_size = 20 if magic_be == FAT_MAGIC else 32
        archs: set[str] = set()
        offset = 8
        for _ in range(nfat_arch):
            if len(raw) < offset + entry_size:
                raise AppInfoError(
                    f"{executable_file}: truncated Mach-O fat header"
                )
            cputype = int.from_bytes(raw[offset : offset + 4], "big")
            archs.add(_cputype_name(cputype))
            offset += entry_size
        return archs

    raise AppInfoError(f"{executable_file}: not a Mach-O executable")


def _declared_archs(plist_data: dict, plist_file: Path) -> list[str]:
    """Extract and validate the ``LSEntry.archs`` declaration."""
    ls_entry = plist_data.get("LSEntry")
    if not isinstance(ls_entry, dict):
        return []
    if "archs" not in ls_entry:
        return []
    archs = ls_entry["archs"]
    if not isinstance(archs, list):
        raise AppInfoError(f"{plist_file}: LSEntry.archs must be an array")
    for element in archs:
        if not isinstance(element, str) or element not in VALID_DECLARED_ARCHS:
            raise AppInfoError(
                f"{plist_file}: invalid architecture declaration {element!r}"
            )
    return sorted(set(archs))


def _check_arch(app_arg: str) -> dict:
    """Verify a bundle's executable against its architecture declaration."""
    contents = _validate_app_bundle(app_arg)

    plist_file = contents / "Info.plist"
    if not plist_file.exists():
        raise AppInfoError(f"{plist_file}: missing Info.plist")
    plist_data = _load_info_plist(plist_file)

    bundle_executable = plist_data.get("CFBundleExecutable")
    if not isinstance(bundle_executable, str):
        raise AppInfoError(
            f"{plist_file}: CFBundleExecutable is missing or not a string"
        )

    executable_rel = f"Contents/MacOS/{bundle_executable}"
    executable_file = contents / "MacOS" / bundle_executable
    if not executable_file.is_file():
        raise AppInfoError(
            f"{executable_file}: declared executable is missing"
            " or not a regular file"
        )

    declared_archs = _declared_archs(plist_data, plist_file)
    actual_archs = sorted(_macho_archs(executable_file) - {"other"})

    declared_set = set(declared_archs)
    actual_set = set(actual_archs)
    if not declared_set:
        match = True
    elif "universal" in declared_set:
        match = len(actual_set) >= 2
    else:
        match = declared_set <= actual_set

    return {
        "executable": executable_rel,
        "declared_archs": declared_archs,
        "actual_archs": actual_archs,
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
        help="Verify a .app bundle's executable architectures against the "
        "LSEntry.archs declaration in its Info.plist.",
    )
    arch_check_parser.add_argument("app", help="path to the .app bundle directory")

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
            report = _check_arch(args.app)
        except AppInfoError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(report))
        return 0

    return 0
