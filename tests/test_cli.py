"""Checks for the documented command-line entry point."""

import json
import os
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
    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "release_workbench", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.base = Path(self.tempdir.name)
        self.app = self.base / "Sample.app"
        contents = self.app / "Contents"
        (contents / "MacOS").mkdir(parents=True)
        (contents / "Resources" / "en.lproj").mkdir(parents=True)
        (contents / "Info.plist").write_text("<plist/>", encoding="utf-8")
        (contents / "MacOS" / "Sample").write_bytes(b"\x00" * 11)
        (contents / "Resources" / "en.lproj" / "Main.nib").write_bytes(b"nib")
        os.symlink("MacOS/Sample", contents / "SampleLink")
        os.symlink("Missing/Target", contents / "DanglingLink")

    def inspect(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return self.invoke("inspect", *arguments)

    def test_manifest_report(self) -> None:
        result = self.inspect(str(self.app))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(result.stdout.count("\n"), 1)
        report = json.loads(result.stdout)
        self.assertEqual(report["app"], str(self.app.resolve()))
        self.assertEqual(report["root"], "Contents")
        self.assertEqual(
            report["entries"],
            [
                {"path": "DanglingLink", "kind": "symlink", "bytes": 0},
                {"path": "Info.plist", "kind": "file", "bytes": 8},
                {"path": "MacOS", "kind": "dir", "bytes": 0},
                {"path": "MacOS/Sample", "kind": "file", "bytes": 11},
                {"path": "Resources", "kind": "dir", "bytes": 0},
                {"path": "Resources/en.lproj", "kind": "dir", "bytes": 0},
                {"path": "Resources/en.lproj/Main.nib", "kind": "file", "bytes": 3},
                {"path": "SampleLink", "kind": "symlink", "bytes": 0},
            ],
        )
        self.assertEqual(report["totalFiles"], 3)
        self.assertEqual(report["totalBytes"], 22)

    def test_entries_sorted_by_code_point(self) -> None:
        result = self.inspect(str(self.app))
        self.assertEqual(result.returncode, 0, result.stderr)
        paths = [entry["path"] for entry in json.loads(result.stdout)["entries"]]
        self.assertEqual(paths, sorted(paths))

    def test_path_normalization(self) -> None:
        messy = str(self.base / ".." / self.base.name / "Sample.app") + "/"
        result = self.inspect(messy)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["app"], str(self.app.resolve()))
        self.assertFalse(report["app"].endswith("/"))

    def test_missing_path_exits_2(self) -> None:
        missing = str(self.base / "Nope.app")
        result = self.inspect(missing)
        self.assertEqual(result.returncode, 2)
        self.assertIn(missing, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_non_directory_exits_2(self) -> None:
        plain = self.base / "Plain.app"
        plain.write_text("not a directory", encoding="utf-8")
        result = self.inspect(str(plain))
        self.assertEqual(result.returncode, 2)
        self.assertIn(str(plain), result.stderr)
        self.assertEqual(result.stdout, "")

    def test_non_app_directory_exits_2(self) -> None:
        other = self.base / "Other.bundle"
        other.mkdir()
        result = self.inspect(str(other))
        self.assertEqual(result.returncode, 2)
        self.assertIn(str(other), result.stderr)
        self.assertEqual(result.stdout, "")

    def test_missing_contents_exits_3(self) -> None:
        empty_app = self.base / "Empty.app"
        empty_app.mkdir()
        result = self.inspect(str(empty_app))
        self.assertEqual(result.returncode, 3)
        self.assertIn(str(empty_app / "Contents"), result.stderr)
        self.assertEqual(result.stdout, "")

    def test_special_file_exits_4(self) -> None:
        os.mkfifo(self.app / "Contents" / "Pipe")
        result = self.inspect(str(self.app))
        self.assertEqual(result.returncode, 4)
        self.assertIn("Pipe", result.stderr)
        self.assertEqual(result.stdout, "")

    @unittest.skipIf(os.geteuid() == 0, "root can read anything")
    def test_unreadable_entry_exits_4(self) -> None:
        locked = self.app / "Contents" / "Resources" / "en.lproj"
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        result = self.inspect(str(self.app))
        self.assertEqual(result.returncode, 4)
        self.assertIn("Resources/en.lproj", result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
