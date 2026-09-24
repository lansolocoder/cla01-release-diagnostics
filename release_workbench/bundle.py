"""Inspection of macOS application bundles (.app)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
from typing import Any

CONTENTS = Path("Contents")
FRAMEWORKS_DIR = CONTENTS / "Frameworks"
PLUGINS_DIR = CONTENTS / "PlugIns"


def _read_plist(path: Path) -> dict[str, Any] | None:
    """Return a plist as a dict, or None when missing/unparseable/not a dict."""
    try:
        with path.open("rb") as handle:
            data = plistlib.load(handle)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _string_value(plist: dict[str, Any] | None, key: str) -> str | None:
    if plist is None:
        return None
    value = plist.get(key)
    return value if isinstance(value, str) else None


def _direct_directories(base: Path, suffix: str) -> list[str]:
    """Names of direct subdirectories of base ending in suffix, sorted and unique."""
    names: set[str] = set()
    try:
        with os.scandir(base) as entries:
            for entry in entries:
                if entry.name.endswith(suffix) and entry.is_dir(follow_symlinks=True):
                    names.add(entry.name)
    except OSError:
        return []
    return sorted(names)


def _relative(path: Path, root: Path) -> str:
    """Path relative to root using '/' separators without a leading './'."""
    return Path(os.path.relpath(path, root)).as_posix()


def _escaping_symlinks(root: Path) -> list[str]:
    """Relative paths of symlinks whose target resolves outside the bundle root."""
    root_abs = Path(os.path.abspath(root))
    found: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            link_path = Path(dirpath) / name
            if not link_path.is_symlink():
                continue
            target = os.readlink(link_path)
            if os.path.isabs(target):
                resolved = os.path.normpath(target)
            else:
                resolved = os.path.normpath(
                    os.path.join(os.path.abspath(dirpath), target)
                )
            try:
                Path(resolved).relative_to(root_abs)
            except ValueError:
                found.add(_relative(link_path, root))
    return sorted(found)


def inspect_bundle(bundle_path: str) -> dict[str, Any]:
    """Inspect a validated .app directory and return the JSON-ready result."""
    root = Path(bundle_path)

    main_plist = _read_plist(root / CONTENTS / "Info.plist")
    bundle_identifier = _string_value(main_plist, "CFBundleIdentifier")
    executable = _string_value(main_plist, "CFBundleExecutable")

    frameworks_root = root / FRAMEWORKS_DIR
    plugins_root = root / PLUGINS_DIR
    frameworks = _direct_directories(frameworks_root, ".framework")
    plugins = _direct_directories(plugins_root, ".appex")

    issues: list[dict[str, str]] = []

    # 1. appex-no-executable (most severe).
    appex_paths: list[str] = []
    for name in plugins:
        appex = plugins_root / name
        appex_plist = _read_plist(appex / CONTENTS / "Info.plist")
        appex_executable = _string_value(appex_plist, "CFBundleExecutable")
        if not appex_executable:
            continue
        executable_file = appex / CONTENTS / "MacOS" / appex_executable
        if not executable_file.exists():
            appex_paths.append(f"Contents/PlugIns/{name}")
    for path in sorted(appex_paths):
        issues.append({"code": "appex-no-executable", "path": path})

    # 2. framework-unversioned.
    framework_paths = [
        f"Contents/Frameworks/{name}"
        for name in frameworks
        if not (frameworks_root / name / "Resources").is_dir()
    ]
    for path in sorted(framework_paths):
        issues.append({"code": "framework-unversioned", "path": path})

    # 3. symlink-escape (least severe).
    for path in _escaping_symlinks(root):
        issues.append({"code": "symlink-escape", "path": path})

    return {
        "bundle": bundle_path,
        "bundleIdentifier": bundle_identifier,
        "executable": executable,
        "frameworks": frameworks,
        "plugins": plugins,
        "issues": issues,
    }


def render_bundle_report(bundle_path: str) -> str:
    return json.dumps(inspect_bundle(bundle_path), ensure_ascii=False, indent=2)
