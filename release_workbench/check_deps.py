"""Check dylib dependencies and architecture slices of Mach-O files in a .app bundle."""

import json
import os
import struct
import sys

_MACHO64_MAGICS = {
    b"\xcf\xfa\xed\xfe": "<",  # 0xfeedfacf, little-endian 64-bit Mach-O
    b"\xfe\xed\xfa\xcf": ">",  # 0xcffaedfe, big-endian 64-bit Mach-O
}
_FAT_MAGICS = {
    b"\xca\xfe\xba\xbe": ">",  # 0xcafebabe, big-endian fat header
    b"\xbe\xba\xfe\xca": "<",  # 0xbebafeca, little-endian fat header
}
_LC_LOAD_DYLIB = 0xC
_LC_LOAD_WEAK_DYLIB = 0x80000018
_LC_REEXPORT_DYLIB = 0x800001F
_LOAD_COMMANDS = {_LC_LOAD_DYLIB, _LC_LOAD_WEAK_DYLIB, _LC_REEXPORT_DYLIB}
_SYSTEM_PREFIXES = ("/usr/lib/", "/System/Library/")

_CPU_NAMES = {0x01000007: "arm64", 0x0100000C: "x86_64"}


def _fail(message: str, exit_code: int) -> int:
    sys.stderr.write(f"error: {message}\n")
    return exit_code


def _collect_files(directory: str, files: list[str]) -> str | None:
    """Append regular-file paths below *directory*; return an offending path on failure."""
    try:
        with os.scandir(directory) as iterator:
            children = list(iterator)
    except OSError:
        return directory
    for child in children:
        try:
            if child.is_symlink():
                continue
            if child.is_dir(follow_symlinks=False):
                failure = _collect_files(child.path, files)
                if failure is not None:
                    return failure
            elif child.is_file(follow_symlinks=False):
                files.append(child.path)
        except OSError:
            return child.path
    return None


def _cpu_name(cputype: int) -> str:
    return _CPU_NAMES.get(cputype, f"0x{cputype:08x}")


def _thin_slice(data: bytes, base: int, endian: str) -> tuple[str, set[str]] | None:
    """Return (cpu name, dylib paths) for the 64-bit Mach-O slice at *base*."""
    if base + 32 > len(data):
        return None
    cputype = struct.unpack_from(endian + "I", data, base + 4)[0]
    ncmds = struct.unpack_from(endian + "I", data, base + 16)[0]
    position = base + 32
    libs: set[str] = set()
    for _ in range(ncmds):
        if position + 8 > len(data):
            return None
        cmd, cmdsize = struct.unpack_from(endian + "II", data, position)
        if cmdsize < 8 or position + cmdsize > len(data):
            return None
        if cmd in _LOAD_COMMANDS:
            name_offset = struct.unpack_from(endian + "I", data, position + 8)[0]
            name_start = position + name_offset
            name_end = data.find(b"\x00", name_start, position + cmdsize)
            if name_offset >= 8 and name_end != -1:
                raw = data[name_start:name_end].decode("utf-8", errors="replace")
                if not raw.startswith(_SYSTEM_PREFIXES):
                    libs.add(raw)
        position += cmdsize
    return (_cpu_name(cputype), libs)


def _parse_macho(data: bytes) -> list[tuple[str, set[str]]] | None:
    """Return per-slice (cpu name, dylib paths); None when *data* is not a Mach-O."""
    magic = data[:4]
    if magic in _MACHO64_MAGICS:
        slice_info = _thin_slice(data, 0, _MACHO64_MAGICS[magic])
        return [slice_info] if slice_info is not None else None
    if magic in _FAT_MAGICS:
        endian = _FAT_MAGICS[magic]
        if len(data) < 8:
            return None
        nfat_arch = struct.unpack_from(endian + "I", data, 4)[0]
        slices: list[tuple[str, set[str]]] = []
        for index in range(nfat_arch):
            entry = 8 + index * 20
            if entry + 20 > len(data):
                break
            offset = struct.unpack_from(endian + "I", data, entry + 8)[0]
            slice_magic = data[offset : offset + 4]
            if slice_magic in _MACHO64_MAGICS:
                slice_info = _thin_slice(data, offset, _MACHO64_MAGICS[slice_magic])
                if slice_info is not None:
                    slices.append(slice_info)
        return slices or None
    return None


def check_deps(app_path: str, require_arch: str | None = None) -> int:
    """Report dylib dependencies and architecture coverage below a .app bundle."""
    if require_arch is not None and require_arch not in ("arm64", "x86_64"):
        return _fail(f"unsupported architecture: {require_arch}", 2)

    normalized = os.path.realpath(app_path)
    if not os.path.isdir(normalized) or not os.path.basename(normalized).endswith(".app"):
        return _fail(f"not an .app application bundle: {app_path}", 2)

    contents = os.path.join(normalized, "Contents")
    if not os.path.isdir(contents):
        return _fail(f"missing Contents directory: {contents}", 3)

    files: list[str] = []
    failure = _collect_files(contents, files)
    if failure is not None:
        if failure == contents:
            return _fail(f"cannot read Contents directory: {contents}", 3)
        return _fail(f"unreadable entry: {failure}", 3)

    bins: list[dict] = []
    total_checked = 0
    total_missing = 0
    for file_path in files:
        try:
            with open(file_path, "rb") as stream:
                data = stream.read()
        except OSError:
            return _fail(f"unreadable entry: {file_path}", 3)
        slices = _parse_macho(data)
        if slices is None:
            continue
        total_checked += 1
        relative = os.path.relpath(file_path, contents).replace(os.sep, "/")
        if require_arch is None:
            merged: set[str] = set()
            for _arch, libs in slices:
                merged |= libs
            bins.append({"path": relative, "arch": None, "libs": sorted(merged)})
            continue
        present = {arch for arch, _libs in slices}
        for arch, libs in slices:
            bins.append({"path": relative, "arch": arch, "libs": sorted(libs)})
        if require_arch not in present:
            bins.append({"path": relative, "arch": None, "missing": require_arch, "libs": []})
            total_missing += 1

    bins.sort(key=lambda entry: (entry["path"], entry["arch"] is not None, entry["arch"]))

    report = {
        "app": normalized,
        "root": "Contents",
        "bins": bins,
        "totalChecked": total_checked,
        "totalMissing": total_missing,
    }
    sys.stdout.write(json.dumps(report, ensure_ascii=False) + "\n")

    if total_checked == 0:
        return 4
    if total_missing:
        return 1
    return 0
