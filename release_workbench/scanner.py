"""Inspect macOS app bundles and their Mach-O executables."""

from __future__ import annotations

import json
import os
import plistlib
from pathlib import Path
from typing import Any

# Mach-O magic numbers (value when the first four bytes are read as big-endian).
FAT_MAGIC = 0xCAFEBABE
MH_MAGIC = 0xFEEDFACE  # 32-bit, big-endian fields
MH_MAGIC_64 = 0xFEEDFACF  # 64-bit, big-endian fields
MH_CIGAM = 0xCEFAEDFE  # 32-bit, little-endian fields
MH_CIGAM_64 = 0xCFFAEDFE  # 64-bit, little-endian fields

LC_LOAD_DYLIB = 0x0C

CPU_ARCH_ABI64 = 0x01000000

CPU_TYPE_NAMES = {
    7: "i386",
    7 | CPU_ARCH_ABI64: "x86_64",
    18: "ppc",
    18 | CPU_ARCH_ABI64: "ppc64",
    12 | CPU_ARCH_ABI64: "arm64",
}


class InvalidAppPackageError(Exception):
    """The path is not a well-formed ``.app`` bundle."""


def _string_value(info: dict[str, Any], key: str) -> str | None:
    """Return *key* only when it is a plain string, otherwise ``None``."""
    value = info.get(key)
    return value if isinstance(value, str) else None


def _cpu_name(cputype: int) -> str:
    return CPU_TYPE_NAMES.get(cputype, f"0x{cputype:x}")


def _parse_thin(data: bytes, base: int) -> tuple[int, list[str]] | None:
    """Parse one Mach-O slice beginning at *base*; ``None`` if not Mach-O."""
    magic = int.from_bytes(data[base:base + 4], "big")
    if magic == MH_MAGIC:
        endian, is_64 = "big", False
    elif magic == MH_MAGIC_64:
        endian, is_64 = "big", True
    elif magic == MH_CIGAM:
        endian, is_64 = "little", False
    elif magic == MH_CIGAM_64:
        endian, is_64 = "little", True
    else:
        return None

    def u32(offset: int) -> int:
        if offset + 4 > len(data):
            raise ValueError("truncated Mach-O header")
        return int.from_bytes(data[offset:offset + 4], endian)

    cputype = u32(base + 4)
    ncmds = u32(base + 16)
    sizeofcmds = u32(base + 20)

    header_size = 32 if is_64 else 28
    commands_start = base + header_size
    commands_end = min(len(data), commands_start + sizeofcmds)

    libraries: list[str] = []
    cursor = commands_start
    for _ in range(ncmds):
        if cursor + 8 > commands_end:
            break
        cmd = int.from_bytes(data[cursor:cursor + 4], endian)
        cmdsize = int.from_bytes(data[cursor + 4:cursor + 8], endian)
        if cmdsize < 8 or cursor + cmdsize > commands_end:
            break
        if cmd == LC_LOAD_DYLIB:
            name_offset = int.from_bytes(data[cursor + 8:cursor + 12], endian)
            name_start = cursor + name_offset
            name_end = cursor + cmdsize
            if commands_start <= name_start < name_end:
                nul = data.find(b"\x00", name_start, name_end)
                if nul != -1:
                    name_end = nul
                libraries.append(data[name_start:name_end].decode("utf-8", "replace"))
        cursor += cmdsize

    return cputype, libraries


def inspect_macho(path: str | os.PathLike[str]) -> tuple[list[str], list[str]]:
    """Return ``(architectures, linked_libraries)`` for a Mach-O file.

    A non-Mach-O file yields two empty lists.
    """
    try:
        data = Path(path).read_bytes()
    except OSError:
        return [], []

    if len(data) < 4:
        return [], []

    architectures: list[str] = []
    libraries: list[str] = []
    magic = int.from_bytes(data[:4], "big")

    if magic == FAT_MAGIC:
        if len(data) < 8:
            return [], []
        nfat = int.from_bytes(data[4:8], "big")
        for index in range(nfat):
            entry = 8 + index * 20  # struct fat_arch is 20 bytes
            if entry + 20 > len(data):
                break
            cputype = int.from_bytes(data[entry:entry + 4], "big")
            slice_offset = int.from_bytes(data[entry + 8:entry + 12], "big")
            architectures.append(_cpu_name(cputype))
            try:
                parsed = _parse_thin(data, slice_offset)
            except ValueError:
                parsed = None
            if parsed is not None:
                libraries.extend(parsed[1])
    else:
        try:
            parsed = _parse_thin(data, 0)
        except ValueError:
            parsed = None
        if parsed is None:
            return [], []
        architectures.append(_cpu_name(parsed[0]))
        libraries.extend(parsed[1])

    return sorted(set(architectures)), libraries


def scan_app(app_path: str) -> dict[str, Any]:
    """Produce the scan result for the ``.app`` bundle at *app_path*."""
    app = Path(app_path)
    if not app.is_dir():
        raise InvalidAppPackageError("app path is not a directory")

    plist_path = app / "Contents" / "Info.plist"
    if not plist_path.is_file():
        raise InvalidAppPackageError("Contents/Info.plist is missing")

    try:
        info = plistlib.loads(plist_path.read_bytes())
    except Exception as exc:  # malformed XML or any other plist parse failure
        raise InvalidAppPackageError(f"Contents/Info.plist is invalid: {exc}") from exc
    if not isinstance(info, dict):
        raise InvalidAppPackageError(
            "Contents/Info.plist is invalid: root object is not a dictionary"
        )

    bundle_identifier = _string_value(info, "CFBundleIdentifier")
    bundle_version = _string_value(info, "CFBundleVersion")
    executable_name = _string_value(info, "CFBundleExecutable")

    issues: list[dict[str, str]] = []
    executable: str | None = None
    architectures: list[str] = []
    linked_libraries: list[str] = []

    if executable_name:
        executable = os.path.join(app_path, "Contents", "MacOS", executable_name)
        if Path(executable).is_file():
            architectures, linked_libraries = inspect_macho(executable)
        else:
            issues.append({
                "code": "missing_executable",
                "detail": (
                    f"Contents/MacOS/{executable_name} does not exist "
                    "or is not a file"
                ),
            })
            executable = None
    else:
        issues.append({
            "code": "missing_executable",
            "detail": "CFBundleExecutable is missing or not a string",
        })

    unique_issues = {
        json.dumps(issue, sort_keys=True, ensure_ascii=False): issue
        for issue in issues
    }
    issues = sorted(
        unique_issues.values(),
        key=lambda issue: (issue["code"], issue["detail"]),
    )

    return {
        "app_path": app_path,
        "bundle_identifier": bundle_identifier,
        "bundle_version": bundle_version,
        "executable": executable,
        "architectures": architectures,
        "linked_libraries": sorted(set(linked_libraries)),
        "issues": issues,
    }
