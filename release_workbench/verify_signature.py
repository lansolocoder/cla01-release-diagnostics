"""Check embedded code signatures of Mach-O files inside a .app bundle."""

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
_LC_CODE_SIGNATURE = 0x1D
_CSMAGIC_EMBEDDED_SIGNATURE = 0xFADE0CC0
_CSMAGIC_CODEDIRECTORY = 0xFADE0C02
_CODE_DIRECTORY_TEAM_VERSION = 0x20200

_KIND_PRIORITY = {"unsigned": 0, "mismatch": 1, "team-mismatch": 2, "metadata": 3}


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


def _thin_signature(data: bytes, base: int, endian: str) -> tuple[int, int] | None:
    """Locate the embedded signature of the 64-bit Mach-O slice at *base*."""
    if base + 32 > len(data):
        return None
    ncmds = struct.unpack_from(endian + "I", data, base + 16)[0]
    position = base + 32
    for _ in range(ncmds):
        if position + 8 > len(data):
            return None
        cmd, cmdsize = struct.unpack_from(endian + "II", data, position)
        if cmdsize < 8 or position + cmdsize > len(data):
            return None
        if cmd == _LC_CODE_SIGNATURE:
            dataoff, datasize = struct.unpack_from(endian + "II", data, position + 8)
            if dataoff == 0 or datasize == 0:
                return None
            return (base + dataoff, datasize)
        position += cmdsize
    return None


def _locate_signatures(data: bytes) -> list[tuple[int, int]] | None:
    """Return embedded signature ranges, or None when *data* is not a recognized Mach-O."""
    magic = data[:4]
    if magic in _MACHO64_MAGICS:
        found = _thin_signature(data, 0, _MACHO64_MAGICS[magic])
        return [found] if found is not None else []
    if magic in _FAT_MAGICS:
        endian = _FAT_MAGICS[magic]
        if len(data) < 8:
            return []
        nfat_arch = struct.unpack_from(endian + "I", data, 4)[0]
        ranges = []
        for index in range(nfat_arch):
            entry = 8 + index * 20
            if entry + 20 > len(data):
                break
            offset = struct.unpack_from(endian + "I", data, entry + 8)[0]
            slice_magic = data[offset : offset + 4]
            if slice_magic in _MACHO64_MAGICS:
                found = _thin_signature(data, offset, _MACHO64_MAGICS[slice_magic])
                if found is not None:
                    ranges.append(found)
        return ranges
    return None


def _read_cstring(blob: bytes, start: int, end: int) -> str:
    if start <= 0 or start >= end:
        raise ValueError("string offset out of range")
    stop = blob.find(b"\x00", start, end)
    if stop == -1:
        raise ValueError("unterminated string")
    return blob[start:stop].decode("utf-8")


def _parse_signature(data: bytes, offset: int, size: int) -> tuple[str, str]:
    """Extract (identity, team id) from the embedded signature superblob."""
    if size < 12 or offset + size > len(data):
        raise ValueError("signature blob out of range")
    blob = data[offset : offset + size]
    magic, length, count = struct.unpack_from(">III", blob, 0)
    if magic != _CSMAGIC_EMBEDDED_SIGNATURE:
        raise ValueError(f"unexpected superblob magic 0x{magic:08x}")
    if length < 12 + count * 8 or length > size:
        raise ValueError("superblob length mismatch")
    directory = None
    for index in range(count):
        entry_offset = struct.unpack_from(">I", blob, 12 + index * 8 + 4)[0]
        if entry_offset + 8 > length:
            continue
        if struct.unpack_from(">I", blob, entry_offset)[0] == _CSMAGIC_CODEDIRECTORY:
            directory = entry_offset
            break
    if directory is None:
        raise ValueError("no code directory in superblob")
    cd_length = struct.unpack_from(">I", blob, directory + 4)[0]
    if cd_length < 24 or directory + cd_length > length:
        raise ValueError("code directory length invalid")
    ident_offset = struct.unpack_from(">I", blob, directory + 20)[0]
    if ident_offset == 0:
        raise ValueError("missing identity slot")
    identity = _read_cstring(blob, directory + ident_offset, directory + cd_length)
    team = ""
    version = struct.unpack_from(">I", blob, directory + 8)[0]
    if version >= _CODE_DIRECTORY_TEAM_VERSION and cd_length >= 52:
        team_offset = struct.unpack_from(">I", blob, directory + 48)[0]
        if team_offset:
            team = _read_cstring(blob, directory + team_offset, directory + cd_length)
    return identity, team


def _check_file(path: str, name: str, team_id: str | None) -> tuple[bool, tuple[str, str] | None]:
    """Return (is_macho, (kind, detail) or None) for the regular file at *path*."""
    with open(path, "rb") as stream:
        data = stream.read()
    ranges = _locate_signatures(data)
    if ranges is None:
        return False, None
    if not ranges:
        return True, ("unsigned", "no embedded code signature")
    problems: list[tuple[str, str]] = []
    parsed = None
    for offset, size in ranges:
        try:
            parsed = _parse_signature(data, offset, size)
            break
        except ValueError as error:
            problems.append(("metadata", f"invalid signature metadata: {error}"))
    if parsed is not None:
        identity, team = parsed
        problems = []
        if name not in identity:
            problems.append(("mismatch", f"identity '{identity}' does not contain '{name}'"))
        if team_id is not None and team != team_id:
            problems.append(("team-mismatch", f"team id '{team}' does not match '{team_id}'"))
    if not problems:
        return True, None
    return True, min(problems, key=lambda problem: _KIND_PRIORITY[problem[0]])


def verify_signature(app_path: str, team_id: str | None = None) -> int:
    """Verify embedded code signatures below the .app bundle at *app_path*."""
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

    issues: list[dict] = []
    saw_macho = False
    for file_path in files:
        relative = os.path.relpath(file_path, contents)
        try:
            is_macho, problem = _check_file(file_path, os.path.basename(file_path), team_id)
        except OSError:
            return _fail(f"unreadable entry: {file_path}", 3)
        if not is_macho:
            continue
        saw_macho = True
        if problem is not None:
            kind, detail = problem
            issues.append({"path": relative, "kind": kind, "detail": detail})
    issues.sort(key=lambda issue: issue["path"])

    report = {
        "app": normalized,
        "root": "Contents",
        "issues": issues,
        "totalChecked": len(files),
    }
    sys.stdout.write(json.dumps(report, ensure_ascii=False) + "\n")

    kinds = {issue["kind"] for issue in issues}
    if kinds & {"mismatch", "metadata"}:
        return 3
    if issues:
        return 1
    if not saw_macho:
        return 4
    return 0
