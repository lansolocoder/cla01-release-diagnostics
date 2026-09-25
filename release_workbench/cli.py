"""Command-line entry point."""

import argparse
import json
import os
import plistlib
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

from . import __version__

INFO_PLIST_PATH = "Contents/Info.plist"

LC_LOAD_DYLIB = 0x0C

# Raw leading bytes -> (bit width, byte order, architecture label).
MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce": (32, "big", "ppc"),
    b"\xce\xfa\xed\xfe": (32, "little", "i386"),
    b"\xfe\xed\xfa\xcf": (64, "big", "ppc64"),
    b"\xcf\xfa\xed\xfe": (64, "little", "x86_64"),
}

MACHO_HEADER_SIZE = {32: 28, 64: 32}
LOAD_COMMAND_HEADER_SIZE = 8
DYLIB_COMMAND_HEADER_SIZE = 24  # cmd, cmdsize, offset, timestamp, current, compat
SCAN_DIRECTORIES = ("MacOS", "Resources")


class AppInfoError(Exception):
    """Diagnostic failure for an ``app-info`` invocation."""


class MachoInfoError(Exception):
    """Diagnostic failure for a ``macho-info`` invocation."""


def _validate_bundle(app_arg: str, error_cls: type[Exception]) -> Path:
    """Validate the common bundle-level preconditions, mirroring ``app-info``."""
    app_path = app_arg  # keep the user-supplied spelling in diagnostics
    app = Path(app_arg)

    if not app.exists():
        raise error_cls(f"{app_path}: path does not exist")
    if not app.is_dir():
        raise error_cls(f"{app_path}: not a directory")
    if not app.name.endswith(".app"):
        raise error_cls(f"{app_path}: bundle name must end with .app")

    contents = app / "Contents"
    if not contents.is_dir():
        raise error_cls(f"{app_path}: missing Contents directory")
    return app


def _collect_app_info(app_arg: str) -> dict:
    """Inspect an ``.app`` bundle and return the JSON-serialisable report."""
    app = _validate_bundle(app_arg, AppInfoError)
    contents = app / "Contents"

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


def _iter_regular_files(directory: str) -> Iterator[str]:
    """Yield regular files beneath ``directory`` recursively, never following symlinks."""
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                yield from _iter_regular_files(entry.path)
            elif entry.is_file(follow_symlinks=False):
                yield entry.path


def _parse_macho_file(file_path: str, data: bytes) -> tuple[str, dict]:
    """Parse one thin Mach-O image; return its architecture label and report entry."""
    bits, endian, arch = MACHO_MAGICS[data[:4]]
    byteorder = "little" if endian == "little" else "big"
    header_size = MACHO_HEADER_SIZE[bits]
    if len(data) < header_size:
        raise MachoInfoError(f"{file_path}: truncated Mach-O header")

    ncmds = int.from_bytes(data[16:20], byteorder)
    dylibs: set[str] = set()
    offset = header_size
    for _ in range(ncmds):
        if offset + LOAD_COMMAND_HEADER_SIZE > len(data):
            raise MachoInfoError(
                f"{file_path}: load command extends past end of file"
            )
        cmd = int.from_bytes(data[offset : offset + 4], byteorder)
        cmdsize = int.from_bytes(data[offset + 4 : offset + 8], byteorder)
        if cmdsize < LOAD_COMMAND_HEADER_SIZE:
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
            name_offset = int.from_bytes(
                data[offset + 8 : offset + 12], byteorder
            )
            current_version = int.from_bytes(
                data[offset + 16 : offset + 20], byteorder
            )
            compat_version = int.from_bytes(
                data[offset + 20 : offset + 24], byteorder
            )
            if current_version != 0 or compat_version != 0:
                raise MachoInfoError(
                    f"{file_path}: dylib command version is not zero"
                )
            if (
                name_offset < DYLIB_COMMAND_HEADER_SIZE
                or name_offset >= cmdsize
            ):
                raise MachoInfoError(f"{file_path}: dylib name offset is invalid")
            name_start = offset + name_offset
            name_end = data.find(b"\0", name_start, offset + cmdsize)
            if name_end < 0:
                raise MachoInfoError(
                    f"{file_path}: dylib name is not NUL-terminated"
                )
            dylibs.add(
                data[name_start:name_end].decode("utf-8", "surrogateescape")
            )
        offset += cmdsize

    return arch, {"bits": bits, "endian": endian, "dylibs": sorted(dylibs)}


def _collect_macho_info(app_arg: str) -> dict:
    """Inspect Mach-O binaries inside an ``.app`` bundle."""
    app = _validate_bundle(app_arg, MachoInfoError)
    contents = app / "Contents"

    architectures: set[str] = set()
    binaries: list[dict] = []
    for subdir in SCAN_DIRECTORIES:
        root = contents / subdir
        if root.is_symlink() or not root.is_dir():
            continue
        for file_path in _iter_regular_files(str(root)):
            try:
                with open(file_path, "rb") as handle:
                    data = handle.read()
            except OSError as exc:
                raise MachoInfoError(
                    f"{file_path}: cannot read file ({exc})"
                ) from exc
            if data[:4] not in MACHO_MAGICS:
                continue
            arch, entry = _parse_macho_file(file_path, data)
            relative = os.path.relpath(file_path, app).replace(os.sep, "/")
            binaries.append({"path": relative, **entry})
            architectures.add(arch)

    binaries.sort(key=lambda item: item["path"])
    return {"architectures": sorted(architectures), "binaries": binaries}


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

    return 0
