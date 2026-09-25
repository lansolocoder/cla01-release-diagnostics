"""Command-line entry point."""

import argparse
import hashlib
import json
import os
import plistlib
import sys
from collections.abc import Iterator, Sequence
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


class ReleaseDiffError(Exception):
    """Diagnostic failure for a ``release-diff`` invocation."""


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


def _iter_bundle_entries(directory: str, root: str) -> Iterator[tuple[str, bool]]:
    """Yield ``(relative path, is_dir)`` for every file and directory.

    Paths are relative to ``root`` with ``/`` separators. Symlinks are
    skipped and never followed.
    """
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                relative = os.path.relpath(entry.path, root).replace(os.sep, "/")
                yield relative, True
                yield from _iter_bundle_entries(entry.path, root)
            elif entry.is_file(follow_symlinks=False):
                relative = os.path.relpath(entry.path, root).replace(os.sep, "/")
                yield relative, False


def _sha256_file(file_path: str) -> str:
    """Return the lowercase hex SHA-256 digest of a regular file."""
    digest = hashlib.sha256()
    try:
        with open(file_path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ReleaseDiffError(f"{file_path}: cannot read file ({exc})") from exc
    return digest.hexdigest()


def _collect_release_diff(old_arg: str, new_arg: str) -> dict:
    """Compare the ``Contents/`` trees of two ``.app`` bundles."""
    bundles = ((old_arg, Path(old_arg)), (new_arg, Path(new_arg)))
    for app_arg, app in bundles:
        if not app.exists():
            raise ReleaseDiffError(f"{app_arg}: path does not exist")
        if not app.is_dir():
            raise ReleaseDiffError(f"{app_arg}: not a directory")
        if not app.name.endswith(".app"):
            raise ReleaseDiffError(f"{app_arg}: bundle name must end with .app")
        if not (app / "Contents").is_dir():
            raise ReleaseDiffError(f"{app_arg}: missing Contents directory")

    old_app, new_app = Path(old_arg), Path(new_arg)

    def scan(app: Path) -> dict[str, bool]:
        return dict(_iter_bundle_entries(str(app / "Contents"), str(app)))

    old_entries = scan(old_app)
    new_entries = scan(new_app)

    files = []
    for path in sorted(set(old_entries) | set(new_entries)):
        old_is_dir = old_entries.get(path)
        new_is_dir = new_entries.get(path)
        if old_is_dir is True and new_is_dir is True:
            continue
        old_full = str(old_app / path)
        new_full = str(new_app / path)
        old_hash: str | None
        new_hash: str | None
        if old_is_dir is None:
            old_hash = None
            if new_is_dir:
                change = "changed"
                new_hash = None
            else:
                change = "added"
                new_hash = _sha256_file(new_full)
        elif new_is_dir is None:
            new_hash = None
            if old_is_dir:
                change = "changed"
                old_hash = None
            else:
                change = "removed"
                old_hash = _sha256_file(old_full)
        elif old_is_dir or new_is_dir:
            # Directory on one side only, or a directory/file type clash.
            change = "changed"
            old_hash = None if old_is_dir else _sha256_file(old_full)
            new_hash = None if new_is_dir else _sha256_file(new_full)
        else:
            old_hash = _sha256_file(old_full)
            new_hash = _sha256_file(new_full)
            change = "unchanged" if old_hash == new_hash else "changed"
        files.append(
            {
                "path": path,
                "change": change,
                "old_sha256": old_hash,
                "new_sha256": new_hash,
            }
        )

    summary = {key: 0 for key in ("added", "removed", "changed", "unchanged")}
    for entry in files:
        summary[entry["change"]] += 1
    return {"files": files, "summary": summary}


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

    release_diff_parser = subparsers.add_parser(
        "release-diff",
        help="Compare two .app bundle releases file by file via SHA-256.",
    )
    release_diff_parser.add_argument("old_app", help="path to the old .app bundle")
    release_diff_parser.add_argument("new_app", help="path to the new .app bundle")

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

    if args.command == "release-diff":
        try:
            report = _collect_release_diff(args.old_app, args.new_app)
        except ReleaseDiffError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(report))
        return 0

    return 0
