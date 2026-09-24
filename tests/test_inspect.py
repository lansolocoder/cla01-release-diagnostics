"""Checks for the inspect subcommand."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InspectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.base = Path(self.tempdir.name)

    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "release_workbench", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def make_bundle(self, name: str = "Demo.app") -> Path:
        bundle = self.base / name
        contents = bundle / "Contents"
        (contents / "MacOS").mkdir(parents=True)
        (contents / "Resources" / "en.lproj").mkdir(parents=True)
        (contents / "Info.plist").write_text("plist", encoding="utf-8")
        (contents / "MacOS" / "demo").write_bytes(b"\x00" * 10)
        (contents / "Resources" / "en.lproj" / "Main.strings").write_bytes(b"abc")
        os.symlink("MacOS/demo", contents / "runner")
        os.symlink("nowhere", contents / "dangling")
        return bundle

    def test_inventory_report(self) -> None:
        bundle = self.make_bundle()
        result = self.invoke("inspect", str(bundle))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        report = json.loads(result.stdout)

        self.assertEqual(report["app"], os.path.realpath(bundle))
        self.assertEqual(report["root"], "Contents")
        self.assertEqual(
            report["entries"],
            [
                {"path": "Info.plist", "kind": "file", "bytes": 5},
                {"path": "MacOS", "kind": "dir", "bytes": 0},
                {"path": "MacOS/demo", "kind": "file", "bytes": 10},
                {"path": "Resources", "kind": "dir", "bytes": 0},
                {"path": "Resources/en.lproj", "kind": "dir", "bytes": 0},
                {"path": "Resources/en.lproj/Main.strings", "kind": "file", "bytes": 3},
                {"path": "dangling", "kind": "symlink", "bytes": 0},
                {"path": "runner", "kind": "symlink", "bytes": 0},
            ],
        )
        paths = [entry["path"] for entry in report["entries"]]
        self.assertEqual(paths, sorted(paths))
        self.assertEqual(report["totalFiles"], 3)
        self.assertEqual(report["totalBytes"], 18)

    def test_path_normalization(self) -> None:
        bundle = self.make_bundle()
        messy = str(self.base / "subdir" / ".." / (bundle.name + "/"))
        (self.base / "subdir").mkdir()
        result = self.invoke("inspect", messy)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["app"], os.path.realpath(bundle))
        self.assertFalse(report["app"].endswith("/"))

    def test_missing_path_is_exit_2(self) -> None:
        missing = str(self.base / "Nope.app")
        result = self.invoke("inspect", missing)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(missing, result.stderr)
        self.assertEqual(len(result.stderr.strip().splitlines()), 1)

    def test_regular_file_is_exit_2(self) -> None:
        target = self.base / "Demo.app"
        target.write_text("not a directory", encoding="utf-8")
        result = self.invoke("inspect", str(target))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(target), result.stderr)

    def test_non_app_directory_is_exit_2(self) -> None:
        target = self.base / "Demo.bundle"
        target.mkdir()
        result = self.invoke("inspect", str(target))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(target), result.stderr)

    def test_missing_contents_is_exit_3(self) -> None:
        bundle = self.base / "Empty.app"
        bundle.mkdir()
        result = self.invoke("inspect", str(bundle))
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout, "")
        self.assertIn(os.path.join(os.path.realpath(bundle), "Contents"), result.stderr)

    def test_special_file_is_exit_4(self) -> None:
        bundle = self.make_bundle()
        os.mkfifo(bundle / "Contents" / "pipe")
        result = self.invoke("inspect", str(bundle))
        self.assertEqual(result.returncode, 4)
        self.assertEqual(result.stdout, "")
        self.assertIn("pipe", result.stderr)
        self.assertEqual(len(result.stderr.strip().splitlines()), 1)

    @unittest.skipIf(os.geteuid() == 0, "permission checks do not apply to root")
    def test_unreadable_contents_is_exit_3(self) -> None:
        bundle = self.make_bundle()
        os.chmod(bundle / "Contents", 0)
        self.addCleanup(os.chmod, bundle / "Contents", 0o755)
        result = self.invoke("inspect", str(bundle))
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout, "")
        self.assertIn(os.path.join(os.path.realpath(bundle), "Contents"), result.stderr)

    @unittest.skipIf(os.geteuid() == 0, "permission checks do not apply to root")
    def test_unreadable_entry_is_exit_4(self) -> None:
        bundle = self.make_bundle()
        os.chmod(bundle / "Contents" / "Resources", 0)
        self.addCleanup(os.chmod, bundle / "Contents" / "Resources", 0o755)
        result = self.invoke("inspect", str(bundle))
        self.assertEqual(result.returncode, 4)
        self.assertEqual(result.stdout, "")
        self.assertIn("Resources", result.stderr)


if __name__ == "__main__":
    unittest.main()
