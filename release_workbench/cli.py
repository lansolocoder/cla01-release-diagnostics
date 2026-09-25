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

LC_LOAD_DYLIB = 0x0C

# Raw magic bytes -> (bits, endian, architecture label).
MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce": (32, "big", "ppc"),
    b"\xce\xfa\xed\xfe": (32, "little", "i386"),
    b"\xfe\xed\xfa\xcf": (64, "big", "ppc64"),
    b"\xcf\xfa\xed\xfe": (64, "little", "x86_64"),
}

MACHO_HEADER_SIZE = {32: 28, 64: 32}
DYLIB_COMMAND_HEADER_SIZE = 24


class AppInfoError(Exception):
    """Diagnostic failure for an ``app-info`` invocation."""


class MachoInfoError(Exception):
    """Diagnostic failure for a ``macho-info`` invocation."""


def _collect_app_info(app_arg: str) -> dict:
    """Inspect an ``.app`` bundle and return the JSON-serialisable report."""
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

    components = sorted({"Contents/" + name for name in os.listdir(contents)})

    plist_file = contents / "Info.plist"
    bundle_id: str | None = None
    executable: str | None = None
    if plist_file.exists():
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


def _iter_bundle_files(directory: str):
    """Yield regular files below ``directory``, never following symlinks."""
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                yield from _iter_bundle_files(entry.path)
            elif entry.is_file(follow_symlinks=False):
                yield entry.path


def _parse_macho_file(file_path: str, data: bytes) -> dict:
    """Parse one Mach-O file and return its report entry plus arch label."""
    bits, endian, arch = MACHO_MAGICS[data[:4]]
    byteorder = "little" if endian == "little" else "big"
    header_size = MACHO_HEADER_SIZE[bits]
    if len(data) < header_size:
        raise MachoInfoError(f"{file_path}: truncated Mach-O header")

    ncmds = int.from_bytes(data[16:20], byteorder)
    dylibs: set[str] = set()
    offset = header_size
    for _ in range(ncmds):
        if offset + 8 > len(data):
            raise MachoInfoError(
                f"{file_path}: load command extends past end of file"
            )
        cmd = int.from_bytes(data[offset : offset + 4], byteorder)
        cmdsize = int.from_bytes(data[offset + 4 : offset + 8], byteorder)
        if cmdsize < 8:
            raise MachoInfoError(
                f"{file_path}: load command size smaller than command header"
            )
        if offset + cmdsize > len(data):
            raise MachoInfoError(
                f"{file_path}: load command extends past end of file"
            )
        if cmd == LC_LOAD_DYLIB:
            if cmdsize < DYLIB_COMMAND_HEADER_SIZE:
                raise MachoInfoError(
                    f"{file_path}: dylib command size smaller than command header"
                )
            name_offset = int.from_bytes(data[offset + 8 : offset + 12], byteorder)
            current_version = int.from_bytes(data[offset + 16 : offset + 20], byteorder)
            compat_version = int.from_bytes(data[offset + 20 : offset + 24], byteorder)
            if current_version != 0 or compat_version != 0:
                raise MachoInfoError(
                    f"{file_path}: dylib command version is not zero"
                )
            if name_offset < DYLIB_COMMAND_HEADER_SIZE or name_offset >= cmdsize:
                raise MachoInfoError(f"{file_path}: dylib name offset is invalid")
            end = data.find(b"\0", offset + name_offset, offset + cmdsize)
            if end < 0:
                raise MachoInfoError(
                    f"{file_path}: dylib name is not NUL-terminated"
                )
            dylibs.add(
                data[offset + name_offset : end].decode("utf-8", "surrogateescape")
            )
        offset += cmdsize

    return {
        "arch": arch,
        "entry": {
            "bits": bits,
            "endian": endian,
            "dylibs": sorted(dylibs),
        },
    }


def _collect_macho_info(app_arg: str) -> dict:
    """Inspect Mach-O binaries inside an ``.app`` bundle."""
    app_path = app_arg  # keep the user-supplied spelling in diagnostics
    app = Path(app_arg)

    if not app.exists():
        raise MachoInfoError(f"{app_path}: path does not exist")
    if not app.is_dir():
        raise MachoInfoError(f"{app_path}: not a directory")
    if not app.name.endswith(".app"):
        raise MachoInfoError(f"{app_path}: bundle name must end with .app")

    contents = app / "Contents"
    if not contents.is_dir():
        raise MachoInfoError(f"{app_path}: missing Contents directory")

    architectures: set[str] = set()
    binaries = []
    for subdir in ("MacOS", "Resources"):
        root = contents / subdir
        if root.is_symlink() or not root.is_dir():
            continue
        for file_path in _iter_bundle_files(str(root)):
            try:
                with open(file_path, "rb") as handle:
                    data = handle.read()
            except OSError as exc:
                raise MachoInfoError(f"{file_path}: cannot read file ({exc})") from exc
            if data[:4] not in MACHO_MAGICS:
                continue
            parsed = _parse_macho_file(file_path, data)
            relative = os.path.relpath(file_path, app).replace(os.sep, "/")
            entry = {"path": relative}
            entry.update(parsed["entry"])
            architectures.add(parsed["arch"])
            binaries.append(entry)

    binaries.sort(key=lambda item: item["path"])
    return {"architectures": sorted(architectures), "binaries": binaries}


def _collect_apps_diff(old_arg: str, new_arg: str) -> dict:
    """Compare two ``.app`` bundles using the app-info/macho-info rules."""
    # Validate and inspect the old bundle fully before touching the new one.
    old_info = _collect_app_info(old_arg)
    old_macho = _collect_macho_info(old_arg)
    new_info = _collect_app_info(new_arg)
    new_macho = _collect_macho_info(new_arg)

    old_components = set(old_info["components"])
    new_components = set(new_info["components"])

    old_archs = set(old_macho["architectures"])
    new_archs = set(new_macho["architectures"])

    old_dylibs = {
        entry["path"]: set(entry["dylibs"]) for entry in old_macho["binaries"]
    }
    new_dylibs = {
        entry["path"]: set(entry["dylibs"]) for entry in new_macho["binaries"]
    }
    old_paths = set(old_dylibs)
    new_paths = set(new_dylibs)

    dylib_changes = []
    for path in sorted(old_paths & new_paths):
        added = sorted(new_dylibs[path] - old_dylibs[path])
        removed = sorted(old_dylibs[path] - new_dylibs[path])
        if added or removed:
            dylib_changes.append({"path": path, "added": added, "removed": removed})

    return {
        "added_components": sorted(new_components - old_components),
        "removed_components": sorted(old_components - new_components),
        "bundle_id_changed": old_info["bundle_id"] != new_info["bundle_id"],
        "executable_changed": old_info["executable"] != new_info["executable"],
        "added_architectures": sorted(new_archs - old_archs),
        "removed_architectures": sorted(old_archs - new_archs),
        "added_binaries": sorted(new_paths - old_paths),
        "removed_binaries": sorted(old_paths - new_paths),
        "dylib_changes": dylib_changes,
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

    macho_info_parser = subparsers.add_parser(
        "macho-info",
        help="List Mach-O binaries in a .app bundle and their linked dylibs.",
    )
    macho_info_parser.add_argument("app", help="path to the .app bundle directory")

    diff_parser = subparsers.add_parser(
        "diff-apps",
        help="Compare two .app bundles (old then new) and emit a JSON report.",
    )
    diff_parser.add_argument("old_app", help="path to the old .app bundle directory")
    diff_parser.add_argument("new_app", help="path to the new .app bundle directory")

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

    if args.command == "macho-info":
        try:
            report = _collect_macho_info(args.app)
        except MachoInfoError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(report))
        return 0

    if args.command == "diff-apps":
        try:
            report = _collect_apps_diff(args.old_app, args.new_app)
        except (AppInfoError, MachoInfoError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(report))
        return 0

    return 0
