"""Checks for the documented command-line entry point."""

import json
import os
import plistlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CommandLineTests(unittest.TestCase):
    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "release_workbench", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_help_and_no_arguments(self) -> None:
        for arguments in [(), ("--help",)]:
            with self.subTest(arguments=arguments):
                result = self.invoke(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--help", result.stdout)
                self.assertIn("--version", result.stdout)
                self.assertEqual(result.stderr, "")

    def test_version(self) -> None:
        result = self.invoke("--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "release-workbench 0.1.0")
        self.assertEqual(result.stderr, "")

    def test_unknown_argument_is_an_error(self) -> None:
        result = self.invoke("--unknown-option")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--unknown-option", result.stderr)
        self.assertEqual(result.stdout, "")


class InspectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "release_workbench", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def make_bundle(self, name: str = "Sample.app", info: dict | None = None) -> Path:
        bundle = self.root / name
        contents = bundle / "Contents"
        contents.mkdir(parents=True)
        if info is not None:
            with (contents / "Info.plist").open("wb") as stream:
                plistlib.dump(info, stream)
        return bundle

    def inspect(self, bundle: Path) -> dict:
        result = self.invoke("inspect", str(bundle))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def test_minimal_bundle(self) -> None:
        bundle = self.make_bundle(info={"CFBundleIdentifier": "com.example.sample",
                                        "CFBundleExecutable": "Sample"})
        report = self.inspect(bundle)
        self.assertEqual(report, {
            "bundle": str(bundle),
            "bundleIdentifier": "com.example.sample",
            "executable": "Sample",
            "frameworks": [],
            "plugins": [],
            "issues": [],
        })

    def test_missing_info_plist_yields_nulls(self) -> None:
        bundle = self.make_bundle()
        report = self.inspect(bundle)
        self.assertIsNone(report["bundleIdentifier"])
        self.assertIsNone(report["executable"])

    def test_unparseable_info_plist_yields_nulls(self) -> None:
        bundle = self.make_bundle()
        (bundle / "Contents" / "Info.plist").write_text("not a plist")
        report = self.inspect(bundle)
        self.assertIsNone(report["bundleIdentifier"])
        self.assertIsNone(report["executable"])

    def test_frameworks_and_plugins_listed_sorted(self) -> None:
        bundle = self.make_bundle()
        frameworks = bundle / "Contents" / "Frameworks"
        plugins = bundle / "Contents" / "PlugIns"
        for name in ("Zed.framework", "Alpha.framework"):
            (frameworks / name / "Resources").mkdir(parents=True)
        (frameworks / "notes.txt").write_text("ignore me")
        for name in ("Beta.appex", "Alpha.appex"):
            (plugins / name).mkdir(parents=True)
        report = self.inspect(bundle)
        self.assertEqual(report["frameworks"], ["Alpha.framework", "Zed.framework"])
        self.assertEqual(report["plugins"], ["Alpha.appex", "Beta.appex"])
        self.assertEqual(report["issues"], [])

    def test_appex_no_executable(self) -> None:
        bundle = self.make_bundle()
        appex = bundle / "Contents" / "PlugIns" / "Share.appex"
        (appex / "Contents" / "MacOS").mkdir(parents=True)
        with (appex / "Contents" / "Info.plist").open("wb") as stream:
            plistlib.dump({"CFBundleExecutable": "Share"}, stream)
        report = self.inspect(bundle)
        self.assertEqual(report["issues"], [
            {"code": "appex-no-executable", "path": "Contents/PlugIns/Share.appex"},
        ])
        (appex / "Contents" / "MacOS" / "Share").write_text("binary")
        self.assertEqual(self.inspect(bundle)["issues"], [])

    def test_framework_unversioned(self) -> None:
        bundle = self.make_bundle()
        (bundle / "Contents" / "Frameworks" / "Bare.framework").mkdir(parents=True)
        report = self.inspect(bundle)
        self.assertEqual(report["issues"], [
            {"code": "framework-unversioned", "path": "Contents/Frameworks/Bare.framework"},
        ])

    def test_symlink_escape(self) -> None:
        bundle = self.make_bundle()
        outside = self.root / "outside.txt"
        outside.write_text("secret")
        macos = bundle / "Contents" / "MacOS"
        macos.mkdir(parents=True)
        os.symlink(outside, macos / "leak")
        os.symlink("../Info.plist", macos / "internal")
        report = self.inspect(bundle)
        self.assertEqual(report["issues"], [
            {"code": "symlink-escape", "path": "Contents/MacOS/leak"},
        ])

    def test_issues_sorted_by_severity_then_path(self) -> None:
        bundle = self.make_bundle()
        frameworks = bundle / "Contents" / "Frameworks"
        (frameworks / "B.framework").mkdir(parents=True)
        (frameworks / "A.framework").mkdir(parents=True)
        appex = bundle / "Contents" / "PlugIns" / "Share.appex"
        (appex / "Contents").mkdir(parents=True)
        with (appex / "Contents" / "Info.plist").open("wb") as stream:
            plistlib.dump({"CFBundleExecutable": "Share"}, stream)
        os.symlink(self.root / "outside.txt", bundle / "Contents" / "escape")
        report = self.inspect(bundle)
        self.assertEqual(report["issues"], [
            {"code": "appex-no-executable", "path": "Contents/PlugIns/Share.appex"},
            {"code": "framework-unversioned", "path": "Contents/Frameworks/A.framework"},
            {"code": "framework-unversioned", "path": "Contents/Frameworks/B.framework"},
            {"code": "symlink-escape", "path": "Contents/escape"},
        ])

    def test_invalid_bundle_path(self) -> None:
        for path in [self.root / "missing.app", self.root / "plain", self.root / "file.app"]:
            if path.name == "plain":
                path.mkdir()
            if path.name == "file.app":
                path.write_text("not a directory")
            with self.subTest(path=path):
                result = self.invoke("inspect", str(path))
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertNotEqual(result.stderr, "")

    def test_extra_positional_argument_is_an_error(self) -> None:
        bundle = self.make_bundle()
        result = self.invoke("inspect", str(bundle), str(bundle))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
