"""Parse a .app bundle and report its component inventory."""

import json
import os
import sys


def _fail(message: str, exit_code: int) -> int:
    sys.stderr.write(f"error: {message}\n")
    return exit_code


def _walk(directory: str, prefix: str, entries: list[dict]) -> str | None:
    """Collect entries below *directory*; return an offending relative path on failure."""
    try:
        with os.scandir(directory) as iterator:
            children = list(iterator)
    except OSError:
        return prefix
    for child in children:
        relative = child.name if not prefix else f"{prefix}/{child.name}"
        try:
            if child.is_symlink():
                kind, size = "symlink", 0
            elif child.is_dir(follow_symlinks=False):
                kind, size = "dir", 0
            elif child.is_file(follow_symlinks=False):
                kind, size = "file", child.stat(follow_symlinks=False).st_size
            else:
                return relative
        except OSError:
            return relative
        entries.append({"path": relative, "kind": kind, "bytes": size})
        if kind == "dir":
            failure = _walk(child.path, relative, entries)
            if failure is not None:
                return failure
    return None


def inspect_app(app_path: str) -> int:
    """Inspect the .app bundle at *app_path* and print its inventory as JSON."""
    normalized = os.path.realpath(app_path)
    if not os.path.isdir(normalized) or not os.path.basename(normalized).endswith(".app"):
        return _fail(f"not an .app application bundle: {app_path}", 2)

    contents = os.path.join(normalized, "Contents")
    if not os.path.isdir(contents):
        return _fail(f"missing Contents directory: {contents}", 3)

    entries: list[dict] = []
    failure = _walk(contents, "", entries)
    if failure is not None:
        if not failure:
            return _fail(f"cannot read Contents directory: {contents}", 3)
        return _fail(f"unreadable or unsupported entry: {failure}", 4)

    entries.sort(key=lambda entry: entry["path"])
    report = {
        "app": normalized,
        "root": "Contents",
        "entries": entries,
        "totalFiles": sum(1 for entry in entries if entry["kind"] == "file"),
        "totalBytes": sum(entry["bytes"] for entry in entries if entry["kind"] == "file"),
    }
    sys.stdout.write(json.dumps(report, ensure_ascii=False) + "\n")
    return 0
