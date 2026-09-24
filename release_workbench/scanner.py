"""Parse ``.app`` bundles and inspect their Mach-O executables."""

from __future__ import annotations

import plistlib
import struct
from pathlib import Path
from typing import BinaryIO

# Mach-O load commands.
LC_LOAD_DYLIB = 0x0C

# Mach-O magic numbers (compared as a big-endian read of the first four bytes).
_MH_MAGIC = 0xFEEDFACE  # 32-bit, big-endian bytes
_MH_CIGAM = 0xCEFAEDFE  # 32-bit, little-endian bytes
_MH_MAGIC_64 = 0xFEEDFACF  # 64-bit, big-endian bytes
_MH_CIGAM_64 = 0xCFFAEDFE  # 64-bit, little-endian bytes
_FAT_MAGIC = 0xCAFEBABE
_FAT_MAGIC_64 = 0xCAFEBABF

_CPU_ARCH_ABI64 = 0x01000000
_CPU_TYPE_NAMES = {
    7: "i386",
    7 | _CPU_ARCH_ABI64: "x86_64",
    12 | _CPU_ARCH_ABI64: "arm64",
    18: "ppc",
    18 | _CPU_ARCH_ABI64: "ppc64",
}

# Guard against absurd/corrupt load-command sizes.
_MAX_LOAD_COMMANDS_SIZE = 64 * 1024 * 1024
_MAX_FAT_SLICES = 256


class InvalidAppPackageError(Exception):
    """Raised when a path is not a well-formed ``.app`` package."""


def scan_app(app_path_text: str) -> dict:
    """Inspect an app bundle and return the scan result dictionary."""
    app_path = Path(app_path_text)
    if not app_path.is_dir():
        raise InvalidAppPackageError(f"{app_path_text} is not a directory")

    plist_path = app_path / "Contents" / "Info.plist"
    if not plist_path.is_file():
        raise InvalidAppPackageError("Contents/Info.plist is missing")

    try:
        with plist_path.open("rb") as fh:
            info = plistlib.load(fh)
    except (plistlib.InvalidFileException, ValueError, OSError) as exc:
        raise InvalidAppPackageError(
            "Contents/Info.plist is not a valid property list"
        ) from exc
    if not isinstance(info, dict):
        info = {}

    def string_value(key: str) -> str | None:
        value = info.get(key)
        return value if isinstance(value, str) else None

    bundle_identifier = string_value("CFBundleIdentifier")
    bundle_version = string_value("CFBundleVersion")
    executable_name = string_value("CFBundleExecutable")

    issues: list[dict] = []
    architectures: list[str] = []
    linked_libraries: list[str] = []
    executable: str | None = None

    if executable_name is None:
        issues.append(
            {
                "code": "missing_executable",
                "detail": "CFBundleExecutable is missing or not a string",
            }
        )
    else:
        executable_path = app_path / "Contents" / "MacOS" / executable_name
        if executable_path.is_file():
            executable = str(executable_path)
            architectures, linked_libraries = inspect_executable(executable_path)
        else:
            issues.append(
                {
                    "code": "missing_executable",
                    "detail": (
                        "executable not found: "
                        f"Contents/MacOS/{executable_name}"
                    ),
                }
            )

    return {
        "app_path": app_path_text,
        "bundle_identifier": bundle_identifier,
        "bundle_version": bundle_version,
        "executable": executable,
        "architectures": sorted(set(architectures)),
        "linked_libraries": sorted(set(linked_libraries)),
        "issues": issues,
    }


def _cpu_type_name(cputype: int) -> str:
    try:
        return _CPU_TYPE_NAMES[cputype]
    except KeyError:
        return hex(cputype)


def _read_at(fh: BinaryIO, offset: int, size: int) -> bytes:
    fh.seek(offset)
    return fh.read(size)


def inspect_executable(path: Path) -> tuple[list[str], list[str]]:
    """Return ``(architectures, linked_libraries)`` for a Mach-O file.

    Non-Mach-O files (and unreadable/truncated ones) yield empty lists.
    """
    architectures: list[str] = []
    linked_libraries: list[str] = []
    try:
        with path.open("rb") as fh:
            magic_bytes = fh.read(4)
            if len(magic_bytes) < 4:
                return [], []
            (magic,) = struct.unpack(">I", magic_bytes)
            if magic in (_FAT_MAGIC, _FAT_MAGIC_64):
                architectures, linked_libraries = _inspect_fat(fh, magic)
            elif magic in (_MH_MAGIC, _MH_CIGAM, _MH_MAGIC_64, _MH_CIGAM_64):
                cputype, libs = _inspect_thin_slice(fh, 0)
                if cputype is not None:
                    architectures = [_cpu_type_name(cputype)]
                linked_libraries = libs
            else:
                return [], []
    except (OSError, struct.error, ValueError):
        return [], []
    return architectures, linked_libraries


def _inspect_fat(fh: BinaryIO, magic: int) -> tuple[list[str], list[str]]:
    is_64 = magic == _FAT_MAGIC_64
    entry_size = 32 if is_64 else 20

    count_bytes = _read_at(fh, 4, 4)
    if len(count_bytes) < 4:
        return [], []
    (nfat_arch,) = struct.unpack(">I", count_bytes)
    nfat_arch = min(nfat_arch, _MAX_FAT_SLICES)

    entries = _read_at(fh, 8, nfat_arch * entry_size)
    architectures: list[str] = []
    linked_libraries: list[str] = []
    for index in range(nfat_arch):
        entry = entries[index * entry_size : (index + 1) * entry_size]
        if len(entry) < entry_size:
            break
        if is_64:
            cputype, _subtype, offset, _size, _align, _reserved = struct.unpack(
                ">iiQQII", entry
            )
        else:
            cputype, _subtype, offset, _size, _align = struct.unpack(
                ">iiIII", entry
            )
        architectures.append(_cpu_type_name(cputype))
        _thin_cputype, libs = _inspect_thin_slice(fh, offset)
        linked_libraries.extend(libs)
    return architectures, linked_libraries


def _inspect_thin_slice(
    fh: BinaryIO, offset: int
) -> tuple[int | None, list[str]]:
    header = _read_at(fh, offset, 32)
    if len(header) < 4:
        return None, []
    (magic,) = struct.unpack(">I", header[:4])
    if magic in (_MH_MAGIC, _MH_MAGIC_64):
        endian = ">"
    elif magic in (_MH_CIGAM, _MH_CIGAM_64):
        endian = "<"
    else:
        return None, []

    is_64 = magic in (_MH_MAGIC_64, _MH_CIGAM_64)
    header_size = 32 if is_64 else 28
    if len(header) < header_size:
        return None, []

    cputype = struct.unpack(endian + "i", header[4:8])[0]
    (ncmds,) = struct.unpack(endian + "I", header[16:20])
    (sizeofcmds,) = struct.unpack(endian + "I", header[20:24])
    sizeofcmds = min(sizeofcmds, _MAX_LOAD_COMMANDS_SIZE)

    commands = _read_at(fh, offset + header_size, sizeofcmds)
    return cputype, _read_load_dylibs(commands, endian, ncmds)


def _read_load_dylibs(
    data: bytes, endian: str, ncmds: int
) -> list[str]:
    libraries: list[str] = []
    position = 0
    for _ in range(ncmds):
        if position + 8 > len(data):
            break
        cmd, cmdsize = struct.unpack_from(endian + "II", data, position)
        if cmdsize < 8:
            break
        if cmd == LC_LOAD_DYLIB and position + 12 <= len(data):
            (name_offset,) = struct.unpack_from(endian + "I", data, position + 8)
            name_start = position + name_offset
            name_end = position + cmdsize
            if name_start < name_end and name_start < len(data):
                raw_name = data[name_start:name_end].split(b"\x00", 1)[0]
                if raw_name:
                    libraries.append(raw_name.decode("utf-8", errors="replace"))
        position += cmdsize
        if position > len(data):
            break
    return libraries
