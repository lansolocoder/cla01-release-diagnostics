"""Check dylib dependencies and architectures of Mach-O files inside a .app bundle."""

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
_DYLIB_COMMANDS = {
    0x0C,  # LC_LOAD_DYLIB
    0x1F,  # LC_REEXPORT_DYLIB
    0x80000018,  # LC_LOAD_WEAK_DYLIB (0x18 | LC_REQ_DYLD)
}
_CPU_NAMES = {
    0x01000007: "arm64",
    0x0100000C: "x86_64",
}
_STRIPPED_PREFIXES = ("/usr/lib/", "/System/Library/")
_ARCH_CHOICES = ("arm64", "x86_64")


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


def _strip_prefix(path: str) -> str:
    for prefix in _STRIPPED_PREFIXES:
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def _thin_slice(data: bytes, base: int, endian: str) -> tuple[int, list[str]] | None:
    """Return (cputype, libs) for the 64-bit Mach-O slice at *base*, or None if malformed."""
    if base + 32 > len(data):
        return None
    cputype = struct.unpack_from(endian + "I", data, base + 4)[0]
    ncmds = struct.unpack_from(endian + "I", data, base + 16)[0]
    position = base + 32
    libs: list[str] = []
    for _ in range(ncmds):
        if position + 8 > len(data):
            return None
        cmd, cmdsize = struct.unpack_from(endian + "II", data, position)
        if cmdsize < 8 or position + cmdsize > len(data):
            return None
        if cmd in _DYLIB_COMMANDS:
            name_offset = struct.unpack_from(endian + "I", data, position + 8)[0]
            start = position + name_offset
            end = position + cmdsize
            if name_offset == 0 or start >= end:
                return None
            stop = data.find(b"\x00", start, end)
            if stop == -1:
                return None
            try:
                libs.append(_strip_prefix(data[start:stop].decode("utf-8")))
            except UnicodeDecodeError:
                return None
        position += cmdsize
    return cputype, sorted(set(libs))


def _parse_macho(data: bytes) -> tuple[bool, list[tuple[int, list[str]]]] | None:
    """Return (is_fat, slices) for *data*, or None when it is not a recognized Mach-O."""
    magic = data[:4]
    if magic in _MACHO64_MAGICS:
        found = _thin_slice(data, 0, _MACHO64_MAGICS[magic])
        return (False, [found]) if found is not None else None
    if magic in _FAT_MAGICS:
        endian = _FAT_MAGICS[magic]
        if len(data) < 8:
            return None
        nfat_arch = struct.unpack_from(endian + "I", data, 4)[0]
        slices = []
        for index in range(nfat_arch):
            entry = 8 + index * 20
            if entry + 20 > len(data):
                break
            offset = struct.unpack_from(endian + "I", data, entry + 8)[0]
            slice_magic = data[offset : offset + 4]
            if slice_magic in _MACHO64_MAGICS:
                found = _thin_slice(data, offset, _MACHO64_MAGICS[slice_magic])
                if found is not None:
                    slices.append(found)
        return True, slices
    return None


def check_deps(app_path: str, require_arch: str | None = None) -> int:
    """Check architectures and dylib dependencies below the .app bundle at *app_path*."""
    if require_arch is not None and require_arch not in _ARCH_CHOICES:
        return _fail(f"invalid architecture: {require_arch}", 2)

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
    for file_path in files:
        try:
            with open(file_path, "rb") as stream:
                data = stream.read()
        except OSError:
            return _fail(f"unreadable entry: {file_path}", 3)
        parsed = _parse_macho(data)
        if parsed is None:
            continue
        is_fat, slices = parsed
        total_checked += 1
        relative = os.path.relpath(file_path, contents).replace(os.sep, "/")
        if require_arch is None:
            merged = sorted({lib for _, libs in slices for lib in libs})
            arch = None if is_fat else _cpu_name(slices[0][0])
            bins.append({"arch": arch, "libs": merged, "path": relative})
            continue
        names = [_cpu_name(cputype) for cputype, _ in slices]
        for name, (_, libs) in zip(names, slices):
            bins.append({"arch": name, "libs": libs, "path": relative})
        if require_arch not in names:
            bins.append({"arch": None, "libs": [], "missing": require_arch, "path": relative})
    bins.sort(key=lambda entry: (entry["path"], entry["arch"] is not None, entry["arch"] or ""))

    total_missing = sum(1 for entry in bins if "missing" in entry)
    report = {
        "app": normalized,
        "root": "Contents",
        "bins": bins,
        "totalChecked": total_checked,
        "totalMissing": total_missing,
    }
    sys.stdout.write(json.dumps(report, ensure_ascii=False) + "\n")

    if total_missing:
        return 1
    if total_checked == 0:
        return 4
    return 0
