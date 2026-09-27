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
LC_CODE_SIGNATURE = 0x1D

# Embedded code-signature blobs are always stored big-endian ("network" order).
CSMAGIC_EMBEDDED_SIGNATURE = 0xFADE0CC0
CSMAGIC_CODEDIRECTORY = 0xFADE0C02
CS_VERSION_SUPPORTS_TEAM_ID = 0x00020200
CSSLOT_CODEDIRECTORY = 0x00000
CSSLOT_ALTERNATE_CODEDIRECTORIES = 0x1000
CSSLOT_ALTERNATE_CODEDIRECTORY_MAX = 5

# Index-table slot types (csslot_type in cs_blobs.h) -> report names.
CS_SLOT_TYPE_NAMES = {
    0x00000: "CodeDirectory",
    0x00001: "InfoSlot",
    0x00002: "RequirementsSlot",
    0x00003: "ResourceDir",
    0x00004: "ApplicationSlot",
    0x00005: "EntitlementsSlot",
    0x00007: "DEREntitlementsSlot",
    0x10000: "SignatureSlot",
    0x10001: "IdentificationSlot",
    0x10002: "TicketSlot",
}

CS_SUPERBLOB_HEADER_SIZE = 12
CS_BLOB_INDEX_SIZE = 8
CS_BLOB_HEADER_SIZE = 8
CS_CODEDIRECTORY_HEADER_SIZE = 44
CS_CODEDIRECTORY_TEAM_OFFSET = 48

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


class SignInfoError(Exception):
    """Diagnostic failure for a ``sign-info`` invocation."""


class DepCheckError(Exception):
    """Diagnostic failure for a ``dep-check`` invocation."""


# Architecture labels accepted by ``dep-check`` (same set as macho-info).
SUPPORTED_ARCH_LABELS = frozenset({"i386", "ppc", "x86_64", "ppc64"})


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


def _find_code_signature(
    file_path: str, data: bytes, bits: int, byteorder: str
) -> tuple[int, int] | None:
    """Locate the ``LC_CODE_SIGNATURE`` payload, returning (offset, size)."""
    header_size = MACHO_HEADER_SIZE[bits]
    ncmds = int.from_bytes(data[16:20], byteorder)
    offset = header_size
    for _ in range(ncmds):
        if offset + 8 > len(data):
            raise SignInfoError(
                f"{file_path}: load command extends past end of file"
            )
        cmd = int.from_bytes(data[offset : offset + 4], byteorder)
        cmdsize = int.from_bytes(data[offset + 4 : offset + 8], byteorder)
        if cmdsize < 8:
            raise SignInfoError(
                f"{file_path}: load command size smaller than command header"
            )
        if offset + cmdsize > len(data):
            raise SignInfoError(
                f"{file_path}: load command extends past end of file"
            )
        if cmd == LC_CODE_SIGNATURE:
            if cmdsize < 16:
                raise SignInfoError(
                    f"{file_path}: code signature command size smaller than header"
                )
            sig_offset = int.from_bytes(data[offset + 8 : offset + 12], byteorder)
            sig_size = int.from_bytes(data[offset + 12 : offset + 16], byteorder)
            return sig_offset, sig_size
        offset += cmdsize
    return None


def _read_blob_cstring(
    file_path: str, blob: bytes, base: int, blob_length: int, field: str
) -> str | None:
    """Read a NUL-terminated string at ``base`` within a signature blob."""
    if base == 0:
        return None
    if base < 0 or base >= blob_length:
        raise SignInfoError(f"{file_path}: code signature {field} offset out of range")
    end = blob.find(b"\0", base, blob_length)
    if end < 0:
        raise SignInfoError(
            f"{file_path}: code signature {field} is not NUL-terminated"
        )
    return blob[base:end].decode("utf-8", "surrogateescape")


def _parse_code_signature(
    file_path: str, data: bytes, sig_offset: int, sig_size: int
) -> dict:
    """Parse the embedded-signature SuperBlob located at ``sig_offset``."""
    # The load command reserves ``sig_size`` bytes (16-byte aligned); the
    # SuperBlob's own length field is authoritative for the directory content.
    if sig_offset < 0 or sig_size < 0 or sig_offset + sig_size > len(data):
        raise SignInfoError(
            f"{file_path}: code signature data extends past end of file"
        )

    if sig_size < CS_SUPERBLOB_HEADER_SIZE:
        raise SignInfoError(
            f"{file_path}: code signature directory extends past end of file"
        )
    magic = int.from_bytes(data[sig_offset : sig_offset + 4], "big")
    if magic != CSMAGIC_EMBEDDED_SIGNATURE:
        raise SignInfoError(
            f"{file_path}: code signature directory has non-embedded magic"
        )
    length = int.from_bytes(data[sig_offset + 4 : sig_offset + 8], "big")
    if length < CS_SUPERBLOB_HEADER_SIZE or length > sig_size:
        raise SignInfoError(
            f"{file_path}: code signature directory length does not match payload"
        )

    blob = data[sig_offset : sig_offset + length]
    count = int.from_bytes(blob[8:12], "big")
    index_end = CS_SUPERBLOB_HEADER_SIZE + count * CS_BLOB_INDEX_SIZE
    if index_end > length:
        raise SignInfoError(
            f"{file_path}: code signature index table extends past directory"
        )

    entry_names: set[str] = set()
    code_directory: bytes | None = None
    for index in range(count):
        entry_offset_pos = CS_SUPERBLOB_HEADER_SIZE + index * CS_BLOB_INDEX_SIZE
        slot_type = int.from_bytes(
            blob[entry_offset_pos : entry_offset_pos + 4], "big"
        )
        slot_offset = int.from_bytes(
            blob[entry_offset_pos + 4 : entry_offset_pos + 8], "big"
        )
        if (
            slot_offset < CS_SUPERBLOB_HEADER_SIZE
            or slot_offset + CS_BLOB_HEADER_SIZE > length
        ):
            raise SignInfoError(
                f"{file_path}: code signature slot offset out of range"
            )
        slot_length = int.from_bytes(
            blob[slot_offset + 4 : slot_offset + 8], "big"
        )
        if slot_length < CS_BLOB_HEADER_SIZE or slot_offset + slot_length > length:
            raise SignInfoError(
                f"{file_path}: code signature slot extends past directory"
            )
        if slot_type == CSSLOT_CODEDIRECTORY:
            code_directory = blob[slot_offset : slot_offset + slot_length]
        if (
            slot_type == CSSLOT_CODEDIRECTORY
            or CSSLOT_ALTERNATE_CODEDIRECTORIES
            <= slot_type
            < CSSLOT_ALTERNATE_CODEDIRECTORIES + CSSLOT_ALTERNATE_CODEDIRECTORY_MAX
        ):
            entry_names.add("CodeDirectory")
        else:
            entry_names.add(
                CS_SLOT_TYPE_NAMES.get(slot_type, f"0x{slot_type:05x}")
            )

    if code_directory is None:
        raise SignInfoError(
            f"{file_path}: code signature directory lacks a CodeDirectory slot"
        )
    if len(code_directory) < CS_CODEDIRECTORY_HEADER_SIZE:
        raise SignInfoError(
            f"{file_path}: code directory extends past end of file"
        )
    if (
        int.from_bytes(code_directory[0:4], "big") != CSMAGIC_CODEDIRECTORY
    ):
        raise SignInfoError(f"{file_path}: code directory has invalid magic")

    version = int.from_bytes(code_directory[8:12], "big")
    cd_length = int.from_bytes(code_directory[4:8], "big")
    if cd_length != len(code_directory):
        raise SignInfoError(
            f"{file_path}: code directory length does not match slot"
        )
    ident_offset = int.from_bytes(code_directory[20:24], "big")
    identifier = _read_blob_cstring(
        file_path, code_directory, ident_offset, cd_length, "identifier"
    )

    team_id: str | None = None
    if version >= CS_VERSION_SUPPORTS_TEAM_ID:
        if cd_length < CS_CODEDIRECTORY_TEAM_OFFSET + 4:
            raise SignInfoError(
                f"{file_path}: code directory too short for team identifier"
            )
        team_offset = int.from_bytes(
            code_directory[CS_CODEDIRECTORY_TEAM_OFFSET : CS_CODEDIRECTORY_TEAM_OFFSET + 4],
            "big",
        )
        team_id = _read_blob_cstring(
            file_path, code_directory, team_offset, cd_length, "team identifier"
        )

    return {
        "identifier": identifier,
        "team_id": team_id,
        "entries": sorted(entry_names),
    }


def _collect_sign_info(app_arg: str) -> dict:
    """Inspect embedded code signatures of Mach-O binaries in a bundle."""
    app_path = app_arg  # keep the user-supplied spelling in diagnostics
    app = Path(app_arg)

    if not app.exists():
        raise SignInfoError(f"{app_path}: path does not exist")
    if not app.is_dir():
        raise SignInfoError(f"{app_path}: not a directory")
    if not app.name.endswith(".app"):
        raise SignInfoError(f"{app_path}: bundle name must end with .app")

    contents = app / "Contents"
    if not contents.is_dir():
        raise SignInfoError(f"{app_path}: missing Contents directory")

    binaries = []
    unsigned_binaries = []
    for subdir in ("MacOS", "Resources"):
        root = contents / subdir
        if root.is_symlink() or not root.is_dir():
            continue
        for file_path in _iter_bundle_files(str(root)):
            try:
                with open(file_path, "rb") as handle:
                    data = handle.read()
            except OSError as exc:
                raise SignInfoError(f"{file_path}: cannot read file ({exc})") from exc
            if data[:4] not in MACHO_MAGICS:
                continue
            bits, endian, _arch = MACHO_MAGICS[data[:4]]
            byteorder = "little" if endian == "little" else "big"
            if len(data) < MACHO_HEADER_SIZE[bits]:
                raise SignInfoError(f"{file_path}: truncated Mach-O header")
            relative = os.path.relpath(file_path, app).replace(os.sep, "/")
            signature = _find_code_signature(file_path, data, bits, byteorder)
            if signature is None:
                binaries.append(
                    {
                        "path": relative,
                        "status": "unsigned",
                        "identifier": None,
                        "team_id": None,
                        "entries": [],
                    }
                )
                unsigned_binaries.append(relative)
                continue
            details = _parse_code_signature(file_path, data, *signature)
            binaries.append({"path": relative, "status": "signed", **details})

    binaries.sort(key=lambda item: item["path"])
    unsigned_binaries.sort()
    return {
        "binaries": binaries,
        "unsigned_binaries": unsigned_binaries,
    }


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


def _collect_dep_check(app_arg: str, target_arch: str) -> dict:
    """Check linked dylib availability and architecture coverage."""
    app_path = app_arg  # keep the user-supplied spelling in diagnostics
    app = Path(app_arg)

    if not app.exists():
        raise DepCheckError(f"{app_path}: path does not exist")
    if not app.is_dir():
        raise DepCheckError(f"{app_path}: not a directory")
    if not app.name.endswith(".app"):
        raise DepCheckError(f"{app_path}: bundle name must end with .app")

    contents = app / "Contents"
    if not contents.is_dir():
        raise DepCheckError(f"{app_path}: missing Contents directory")

    if target_arch not in SUPPORTED_ARCH_LABELS:
        raise DepCheckError(f"{target_arch}: unknown architecture label")

    frameworks_root = os.path.normpath(str(contents / "Frameworks"))

    external_dylibs: set[str] = set()
    unresolved_dylibs: set[str] = set()
    missing_arch_binaries: set[str] = set()

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
            if parsed["arch"] != target_arch:
                missing_arch_binaries.add(relative)
            for name in parsed["entry"]["dylibs"]:
                if name.startswith("@"):
                    unresolved_dylibs.add(name)
                    continue
                if not os.path.isabs(name):
                    continue
                normalized = os.path.normpath(name)
                if normalized == frameworks_root or normalized.startswith(
                    frameworks_root + os.sep
                ):
                    continue
                external_dylibs.add(name)

    missing_dylibs = {
        name
        for name in external_dylibs
        if not name.startswith(("/usr/lib/", "/System/"))
        and not os.path.exists(name)
    }

    if missing_dylibs:
        status = "missing-deps"
    elif missing_arch_binaries:
        status = "arch-mismatch"
    else:
        status = "ok"

    return {
        "target_arch": target_arch,
        "missing_arch_binaries": sorted(missing_arch_binaries),
        "external_dylibs": sorted(external_dylibs),
        "missing_dylibs": sorted(missing_dylibs),
        "unresolved_dylibs": sorted(unresolved_dylibs),
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

    sign_info_parser = subparsers.add_parser(
        "sign-info",
        help="Inspect embedded code signatures of Mach-O binaries in a .app bundle.",
    )
    sign_info_parser.add_argument("app", help="path to the .app bundle directory")

    dep_check_parser = subparsers.add_parser(
        "dep-check",
        help="Check linked dylib availability and target architecture coverage.",
    )
    dep_check_parser.add_argument("app", help="path to the .app bundle directory")
    dep_check_parser.add_argument(
        "arch",
        help="target architecture label (i386, ppc, x86_64, ppc64)",
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

    if args.command == "sign-info":
        try:
            report = _collect_sign_info(args.app)
        except SignInfoError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(report))
        return 0

    if args.command == "dep-check":
        try:
            report = _collect_dep_check(args.app, args.arch)
        except (DepCheckError, MachoInfoError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(report))
        return 0

    return 0
