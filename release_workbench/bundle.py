"""Inspect the component layout of a macOS .app bundle."""

from __future__ import annotations

import os
import plistlib
from pathlib import Path

# Issue codes ordered from most to least severe.
ISSUE_ORDER = ("appex-no-executable", "framework-unversioned", "symlink-escape")


def _read_info_plist(directory: Path) -> dict | None:
    """Return the parsed Contents/Info.plist of a bundle, or None if unusable."""
    try:
        with (directory / "Contents" / "Info.plist").open("rb") as stream:
            data = plistlib.load(stream)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _string_value(info: dict | None, key: str) -> str | None:
    if not info:
        return None
    value = info.get(key)
    return value if isinstance(value, str) else None


def _component_names(directory: Path, suffix: str) -> list[str]:
    """Names of direct subdirectories of `directory` ending with `suffix`."""
    try:
        entries = [
            entry.name
            for entry in os.scandir(directory)
            if entry.is_dir() and entry.name.endswith(suffix)
        ]
    except OSError:
        return []
    return sorted(set(entries))


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _symlink_escapes(root: Path) -> list[str]:
    """Relative paths of symlinks anywhere in the bundle pointing outside it."""
    root_real = Path(os.path.realpath(root))
    escapes: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in (*dirnames, *filenames):
            candidate = Path(dirpath) / name
            if not candidate.is_symlink():
                continue
            target = Path(os.path.realpath(candidate))
            if target != root_real and root_real not in target.parents:
                escapes.append(_relative(candidate, root))
    return escapes


def inspect_bundle(bundle: Path) -> dict:
    """Build the inspection report for a validated .app bundle directory."""
    info = _read_info_plist(bundle)
    frameworks_dir = bundle / "Contents" / "Frameworks"
    plugins_dir = bundle / "Contents" / "PlugIns"
    frameworks = _component_names(frameworks_dir, ".framework")
    plugins = _component_names(plugins_dir, ".appex")

    issues: list[dict] = []
    for name in plugins:
        appex_info = _read_info_plist(plugins_dir / name)
        executable = _string_value(appex_info, "CFBundleExecutable")
        if executable and not (plugins_dir / name / "Contents" / "MacOS" / executable).exists():
            issues.append({"code": "appex-no-executable", "path": f"Contents/PlugIns/{name}"})
    for name in frameworks:
        if not (frameworks_dir / name / "Resources").is_dir():
            issues.append({"code": "framework-unversioned", "path": f"Contents/Frameworks/{name}"})
    for path in _symlink_escapes(bundle):
        issues.append({"code": "symlink-escape", "path": path})

    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for issue in issues:
        key = (issue["code"], issue["path"])
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    unique.sort(key=lambda issue: (ISSUE_ORDER.index(issue["code"]), issue["path"]))

    return {
        "bundleIdentifier": _string_value(info, "CFBundleIdentifier"),
        "executable": _string_value(info, "CFBundleExecutable"),
        "frameworks": frameworks,
        "plugins": plugins,
        "issues": unique,
    }
