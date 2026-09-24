"""Recursive component listing for .app bundles."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys


class InspectError(Exception):
    """A fatal inspection problem carrying its process exit code."""

    def __init__(self, exit_code: int, message: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.message = message


def _normalize_app_path(app_arg: str) -> Path:
    """Resolve the .app root to a canonical absolute path and validate it."""
    app_path = Path(app_arg).resolve()
    if not app_path.exists():
        raise InspectError(2, f"error: path does not exist: {app_arg}")
    if not app_path.is_dir():
        raise InspectError(2, f"error: not a directory: {app_arg}")
    if not app_path.name.endswith(".app"):
        raise InspectError(2, f"error: not an .app bundle directory: {app_arg}")
    return app_path


def _collect_entries(contents: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []

    def walk(directory: Path, prefix: str) -> None:
        try:
            with os.scandir(directory) as iterator:
                children = list(iterator)
        except OSError:
            if not prefix:
                raise InspectError(
                    3, f"error: cannot read Contents directory: {directory}"
                ) from None
            raise InspectError(4, f"error: cannot read entry: {prefix}") from None
        for child in children:
            relative = f"{prefix}/{child.name}" if prefix else child.name
            try:
                metadata = child.stat(follow_symlinks=False)
            except OSError:
                raise InspectError(
                    4, f"error: cannot read metadata: {relative}"
                ) from None
            mode = metadata.st_mode
            if stat.S_ISREG(mode):
                kind, size = "file", metadata.st_size
            elif stat.S_ISDIR(mode):
                kind, size = "dir", 0
            elif stat.S_ISLNK(mode):
                kind, size = "symlink", 0
            else:
                raise InspectError(
                    4, f"error: unsupported special file: {relative}"
                )
            entries.append({"path": relative, "kind": kind, "bytes": size})
            if kind == "dir":
                walk(Path(child.path), relative)

    walk(contents, "")
    entries.sort(key=lambda entry: entry["path"])
    return entries


def inspect_app(app_arg: str) -> dict[str, object]:
    """Build the component manifest report for the .app at ``app_arg``."""
    app_path = _normalize_app_path(app_arg)
    contents = app_path / "Contents"
    if not contents.is_dir():
        raise InspectError(3, f"error: missing Contents directory: {contents}")
    try:
        with os.scandir(contents):
            pass
    except OSError:
        raise InspectError(3, f"error: cannot read Contents directory: {contents}") from None
    entries = _collect_entries(contents)
    files = [entry for entry in entries if entry["kind"] == "file"]
    return {
        "app": str(app_path),
        "root": "Contents",
        "entries": entries,
        "totalFiles": len(files),
        "totalBytes": sum(entry["bytes"] for entry in files),
    }


def run_inspect(app_arg: str) -> int:
    """Print the manifest report for ``app_arg`` as a single JSON object."""
    try:
        report = inspect_app(app_arg)
    except InspectError as error:
        print(error.message, file=sys.stderr)
        return error.exit_code
    sys.stdout.write(json.dumps(report, ensure_ascii=False) + "\n")
    return 0
