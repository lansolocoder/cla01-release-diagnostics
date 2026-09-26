"""Checks for the documented command-line entry point."""

import json
from pathlib import Path
import plistlib
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


def _write_plist(path: Path, value: object) -> None:
    path.write_bytes(plistlib.dumps(value, fmt=plistlib.FMT_XML))


class AppInfoTests(unittest.TestCase):
    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "release_workbench", "app-info", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_ok_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Example.app"
            contents = app / "Contents"
            (contents / "MacOS").mkdir(parents=True)
            (contents / "Resources").mkdir()
            _write_plist(
                contents / "Info.plist",
                {
                    "CFBundleIdentifier": "com.example.App",
                    "CFBundleExecutable": "Example",
                },
            )
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        report = json.loads(result.stdout)
        self.assertEqual(
            report,
            {
                "bundle_id": "com.example.App",
                "executable": "Example",
                "info_plist": "Contents/Info.plist",
                "components": [
                    "Contents/Info.plist",
                    "Contents/MacOS",
                    "Contents/Resources",
                ],
                "status": "ok",
            },
        )
        self.assertEqual(result.stdout.count("\n"), 1)

    def test_single_component_listed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Dup.app"
            contents = app / "Contents"
            contents.mkdir(parents=True)
            _write_plist(contents / "Info.plist", {})
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["components"], ["Contents/Info.plist"])

    def test_missing_info_plist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Empty.app"
            (app / "Contents" / "MacOS").mkdir(parents=True)
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "missing-info-plist")
        self.assertIsNone(report["bundle_id"])
        self.assertIsNone(report["executable"])
        self.assertEqual(report["info_plist"], "Contents/Info.plist")
        self.assertEqual(report["components"], ["Contents/MacOS"])

    def test_non_string_values_become_null(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Typed.app"
            contents = app / "Contents"
            contents.mkdir(parents=True)
            _write_plist(
                contents / "Info.plist",
                {"CFBundleIdentifier": 42, "CFBundleExecutable": ["x"]},
            )
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "ok")
        self.assertIsNone(report["bundle_id"])
        self.assertIsNone(report["executable"])

    def test_path_does_not_exist(self) -> None:
        result = self.invoke("/nonexistent/Nope.app")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("/nonexistent/Nope.app", result.stderr)

    def test_not_a_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "File.app"
            app.write_text("not a bundle")
            result = self.invoke(str(app))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(app), result.stderr)

    def test_name_not_app_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "Example"
            (bundle / "Contents").mkdir(parents=True)
            result = self.invoke(str(bundle))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(bundle), result.stderr)

    def test_missing_contents_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "NoContents.app"
            app.mkdir()
            result = self.invoke(str(app))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(app), result.stderr)

    def test_invalid_info_plist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Broken.app"
            contents = app / "Contents"
            contents.mkdir(parents=True)
            (contents / "Info.plist").write_text("<<< not a plist >>>")
            result = self.invoke(str(app))
            plist_path = str(contents / "Info.plist")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(plist_path, result.stderr)

    def test_info_plist_root_not_dictionary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Array.app"
            contents = app / "Contents"
            contents.mkdir(parents=True)
            _write_plist(contents / "Info.plist", ["a", "b"])
            result = self.invoke(str(app))
            plist_path = str(contents / "Info.plist")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(plist_path, result.stderr)

    def test_extra_option_is_an_error(self) -> None:
        result = self.invoke("Example.app", "--bogus")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("--bogus", result.stderr)


class CompareTests(unittest.TestCase):
    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "release_workbench", "compare", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def _make_app(
        self, root: Path, name: str, components: dict[str, object], plist: object
    ) -> Path:
        app = root / name
        contents = app / "Contents"
        contents.mkdir(parents=True)
        for entry, kind in components.items():
            target = contents / entry
            if kind == "dir":
                target.mkdir()
            else:
                target.write_text(str(kind))
        if plist is not _NO_PLIST:
            _write_plist(contents / "Info.plist", plist)
        return app

    def test_match(self) -> None:
        plist = {
            "CFBundleIdentifier": "com.example.App",
            "CFBundleExecutable": "App",
            "CFBundleShortVersionString": "1.0",
            "CFBundleVersion": "100",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            components = {"MacOS": "dir", "Resources": "dir"}
            old = self._make_app(root, "Old.app", dict(components), dict(plist))
            new = self._make_app(root, "New.app", dict(components), dict(plist))
            result = self.invoke(str(old), str(new))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(
            report,
            {
                "old": str(old),
                "new": str(new),
                "added": [],
                "removed": [],
                "changed": [],
                "status": "match",
            },
        )
        self.assertEqual(result.stdout.count("\n"), 1)

    def test_added_and_removed_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self._make_app(
                root,
                "Old.app",
                {"MacOS": "dir", "Resources": "dir"},
                {},
            )
            new = self._make_app(
                root,
                "New.app",
                {"Frameworks": "dir", "MacOS": "dir"},
                {},
            )
            result = self.invoke(str(old), str(new))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["added"], ["Contents/Frameworks"])
        self.assertEqual(report["removed"], ["Contents/Resources"])
        self.assertEqual(report["status"], "differs")

    def test_one_sided_key_change_is_differs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self._make_app(
                root, "Old.app", {}, {"CFBundleVersion": "100"}
            )
            new = self._make_app(root, "New.app", {}, {})
            result = self.invoke(str(old), str(new))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(
            report["changed"],
            [{"key": "CFBundleVersion", "old": "100", "new": None}],
        )
        self.assertEqual(report["status"], "differs")

    def test_newly_added_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self._make_app(root, "Old.app", {}, {})
            new = self._make_app(
                root, "New.app", {}, {"CFBundleShortVersionString": "1.1"}
            )
            result = self.invoke(str(old), str(new))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(
            report["changed"],
            [{"key": "CFBundleShortVersionString", "old": None, "new": "1.1"}],
        )
        self.assertEqual(report["status"], "differs")

    def test_both_sides_unequal_is_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self._make_app(
                root,
                "Old.app",
                {"MacOS": "dir"},
                {"CFBundleVersion": "100", "CFBundleShortVersionString": "1.0"},
            )
            new = self._make_app(
                root,
                "New.app",
                {"Frameworks": "dir"},
                {"CFBundleVersion": "200", "CFBundleShortVersionString": "1.0"},
            )
            result = self.invoke(str(old), str(new))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["changed"], [])
        self.assertEqual(report["added"], ["Contents/Frameworks"])
        self.assertEqual(report["removed"], ["Contents/MacOS"])
        self.assertEqual(report["status"], "conflict")

    def test_changed_keys_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self._make_app(
                root,
                "Old.app",
                {},
                {
                    "CFBundleIdentifier": "com.example.App",
                    "CFBundleExecutable": "App",
                    "CFBundleShortVersionString": "1.0",
                    "CFBundleVersion": "100",
                },
            )
            new = self._make_app(root, "New.app", {}, {})
            result = self.invoke(str(old), str(new))
        self.assertEqual(result.returncode, 0, result.stderr)
        keys = [entry["key"] for entry in json.loads(result.stdout)["changed"]]
        self.assertEqual(
            keys,
            [
                "CFBundleExecutable",
                "CFBundleIdentifier",
                "CFBundleShortVersionString",
                "CFBundleVersion",
            ],
        )

    def test_missing_info_plist_treats_keys_as_null(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self._make_app(
                root, "Old.app", {"MacOS": "dir"}, {"CFBundleVersion": "100"}
            )
            new = self._make_app(root, "New.app", {"MacOS": "dir"}, _NO_PLIST)
            result = self.invoke(str(old), str(new))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["removed"], ["Contents/Info.plist"])
        self.assertEqual(
            report["changed"],
            [{"key": "CFBundleVersion", "old": "100", "new": None}],
        )
        self.assertEqual(report["status"], "differs")

    def test_non_string_values_become_null(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self._make_app(
                root, "Old.app", {}, {"CFBundleVersion": 100}
            )
            new = self._make_app(
                root, "New.app", {}, {"CFBundleVersion": "200"}
            )
            result = self.invoke(str(old), str(new))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        # old side non-string -> null; new side string -> one-sided change
        self.assertEqual(
            report["changed"],
            [{"key": "CFBundleVersion", "old": None, "new": "200"}],
        )
        self.assertEqual(report["status"], "differs")

    def test_invalid_bundle_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid = self._make_app(root, "New.app", {}, {})
            result = self.invoke(str(root / "Missing.app"), str(valid))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("Missing.app", result.stderr)

    def test_invalid_info_plist_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self._make_app(root, "Old.app", {}, {})
            new = root / "New.app"
            (new / "Contents").mkdir(parents=True)
            (new / "Contents" / "Info.plist").write_text("<<< not plist >>>")
            plist_path = str(new / "Contents" / "Info.plist")
            result = self.invoke(str(old), str(new))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(plist_path, result.stderr)

    def test_extra_option_is_an_error(self) -> None:
        result = self.invoke("Old.app", "New.app", "--bogus")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("--bogus", result.stderr)

    def test_missing_positional_is_an_error(self) -> None:
        result = self.invoke("Old.app")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("new-app", result.stderr)


_NO_PLIST = object()


if __name__ == "__main__":
    unittest.main()
