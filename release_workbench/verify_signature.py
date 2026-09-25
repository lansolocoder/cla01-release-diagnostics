"""Verify embedded code signatures of Mach-O files inside a .app bundle."""

import json
import os
import struct
import sys

# Recognized Mach-O magics, mapped to the byte order of the header fields.
_THIN_MAGICS = {
    b"\xcf\xfa\xed\xfe": "<",  # MH_MAGIC_64 (0xfeedfacf)
    b"\xfe\xed\xfa\xcf": ">",  # MH_CIGAM_64 (0xcffaedfe)
}
_FAT_MAGICS = {
    b"\xca\xfe\xba\xbe": ">",  # FAT_MAGIC (0xcafebabe)
    b"\xbe\xba\xfe\xca": "<",  # FAT_CIGAM (0xbebafeca)
}

_LC_CODE_SIGNATURE = 0x1D
_CSMAGIC_EMBEDDED_SIGNATURE = 0xFADE0CC0
_CSMAGIC_CODEDIRECTORY = 0xFADE0C02
_CSSLOT_CODEDIRECTORY = 0
_CD_VERSION_TEAM_ID = 0x20200

_KIND_RANK = {"unsigned": 0, "mismatch": 1, "team-mismatch": 2, "metadata": 3}


class _MetadataError(Exception):
    """A signature block or Mach-O structure exists but cannot be parsed."""


def _fail(message: str, exit_code: int) -> int:
    sys.stderr.write(f"error: {message}\n")
    return exit_code


def _read_c_string(blob: bytes, start: int, end: int) -> str:
    """Decode the NUL-terminated UTF-8 string at *start* within ``blob[:end]``."""
    if start <= 0 or start >= end:
        raise _MetadataError("missing string slot")
    stop = blob.find(b"\x00", start, end)
    if stop < 0:
        raise _MetadataError("unterminated string")
    try:
        return blob[start:stop].decode("utf-8")
    except UnicodeDecodeError:
        raise _MetadataError("invalid UTF-8 string") from None


def _parse_code_directory(blob: bytes, offset: int, end: int) -> tuple[str, str]:
    """Return (identity, team id) from the code directory at *offset*."""
    if offset + 24 > end:
        raise _MetadataError("truncated code directory")
    magic, length, version = struct.unpack_from(">III", blob, offset)
    if magic != _CSMAGIC_CODEDIRECTORY:
        raise _MetadataError("bad code directory magic")
    if length < 24 or offset + length > end:
        raise _MetadataError("bad code directory length")
    cd_end = offset + length
    ident_offset = struct.unpack_from(">I", blob, offset + 20)[0]
    if not ident_offset:
        raise _MetadataError("missing identity slot")
    identity = _read_c_string(blob, offset + ident_offset, cd_end)
    team = ""
    if version >= _CD_VERSION_TEAM_ID and length >= 52:
        team_offset = struct.unpack_from(">I", blob, offset + 48)[0]
        if team_offset:
            team = _read_c_string(blob, offset + team_offset, cd_end)
    return identity, team


def _parse_signature(blob: bytes) -> tuple[str, str]:
    """Return (identity, team id) from an embedded signature superblob."""
    if len(blob) < 12:
        raise _MetadataError("truncated signature superblob")
    magic, length, count = struct.unpack_from(">III", blob, 0)
    if magic != _CSMAGIC_EMBEDDED_SIGNATURE:
        raise _MetadataError("bad signature superblob magic")
    if length < 12 or length > len(blob):
        raise _MetadataError("bad signature superblob length")
    if 12 + count * 8 > length:
        raise _MetadataError("truncated superblob index")
    for index in range(count):
        slot_type, slot_offset = struct.unpack_from(">II", blob, 12 + index * 8)
        if slot_type == _CSSLOT_CODEDIRECTORY:
            return _parse_code_directory(blob, slot_offset, length)
    raise _MetadataError("code directory slot missing")


def _find_signature_command(data: bytes, endian: str) -> tuple[int, int] | None:
    """Return the (offset, size) of the LC_CODE_SIGNATURE payload, or None."""
    if len(data) < 32:
        raise _MetadataError("truncated Mach-O header")
    ncmds = struct.unpack_from(endian + "I", data, 16)[0]
    offset = 32
    for _ in range(ncmds):
        if offset + 8 > len(data):
            raise _MetadataError("truncated load command")
        cmd, cmdsize = struct.unpack_from(endian + "II", data, offset)
        if cmdsize < 8 or offset + cmdsize > len(data):
            raise _MetadataError("invalid load command size")
        if cmd == _LC_CODE_SIGNATURE:
            if cmdsize < 16:
                raise _MetadataError("truncated code signature command")
            return struct.unpack_from(endian + "II", data, offset + 8)
        offset += cmdsize
    return None


def _slices(data: bytes):
    """Yield (offset, size, endian) for each recognized thin 64-bit Mach-O in *data*."""
    magic = data[:4]
    if magic in _THIN_MAGICS:
        yield 0, len(data), _THIN_MAGICS[magic]
        return
    endian = _FAT_MAGICS[magic]
    if len(data) < 8:
        raise _MetadataError("truncated fat header")
    nfat_arch = struct.unpack_from(endian + "I", data, 4)[0]
    if 8 + nfat_arch * 20 > len(data):
        raise _MetadataError("truncated fat arch table")
    for index in range(nfat_arch):
        offset, size = struct.unpack_from(endian + "II", data, 8 + index * 20 + 8)
        if size < 4 or offset + size > len(data):
            raise _MetadataError("fat arch slice out of range")
        slice_magic = data[offset : offset + 4]
        if slice_magic in _THIN_MAGICS:
            yield offset, size, _THIN_MAGICS[slice_magic]


def _check_macho(data: bytes, name: str, team_id: str | None) -> list[tuple[str, str]]:
    """Return all (kind, detail) problems found in one Mach-O file."""
    problems: list[tuple[str, str]] = []
    examined = False
    for offset, size, endian in _slices(data):
        examined = True
        slice_data = data[offset : offset + size]
        location = _find_signature_command(slice_data, endian)
        if location is None:
            problems.append(("unsigned", "no embedded code signature"))
            continue
        dataoff, datasize = location
        if datasize == 0:
            problems.append(("unsigned", "no embedded code signature"))
            continue
        if dataoff + datasize > len(slice_data):
            problems.append(("metadata", "signature blob out of range"))
            continue
        try:
            identity, team = _parse_signature(slice_data[dataoff : dataoff + datasize])
        except _MetadataError as exc:
            problems.append(("metadata", f"unreadable signature metadata: {exc}"))
            continue
        if name not in identity:
            problems.append(
                ("mismatch", f"identity {identity!r} does not contain file name {name!r}")
            )
        if team_id is not None and team != team_id:
            problems.append(
                ("team-mismatch", f"team id {team!r} does not match expected {team_id!r}")
            )
    if not examined:
        problems.append(("unsigned", "no embedded code signature"))
    return problems


def _collect_files(directory: str, prefix: str, files: list[tuple[str, str]]) -> str | None:
    """Collect regular files below *directory*; return an offending path on failure."""
    try:
        with os.scandir(directory) as iterator:
            children = list(iterator)
    except OSError:
        return directory
    for child in children:
        relative = child.name if not prefix else f"{prefix}/{child.name}"
        try:
            if child.is_symlink():
                continue
            if child.is_dir(follow_symlinks=False):
                failure = _collect_files(child.path, relative, files)
                if failure is not None:
                    return failure
            elif child.is_file(follow_symlinks=False):
                files.append((relative, child.path))
        except OSError:
            return child.path
    return None


def verify_signature(app_path: str, team_id: str | None = None) -> int:
    """Check embedded code signatures under the .app bundle at *app_path*."""
    normalized = os.path.realpath(app_path)
    if not os.path.isdir(normalized) or not os.path.basename(normalized).endswith(".app"):
        return _fail(f"not an .app application bundle: {app_path}", 2)

    contents = os.path.join(normalized, "Contents")
    if not os.path.isdir(contents):
        return _fail(f"missing Contents directory: {contents}", 3)

    files: list[tuple[str, str]] = []
    failure = _collect_files(contents, "", files)
    if failure is not None:
        return _fail(f"unreadable entry: {failure}", 3)

    issues: list[dict] = []
    macho_found = False
    for relative, path in files:
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            return _fail(f"unreadable file: {path}", 3)
        magic = data[:4]
        if magic not in _THIN_MAGICS and magic not in _FAT_MAGICS:
            continue
        macho_found = True
        try:
            problems = _check_macho(data, os.path.basename(path), team_id)
        except _MetadataError as exc:
            problems = [("metadata", f"unparsable Mach-O: {exc}")]
        if problems:
            kind, detail = min(problems, key=lambda item: _KIND_RANK[item[0]])
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
    if not macho_found:
        return 4
    return 0
