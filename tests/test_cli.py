"""Checks for the documented command-line entry point."""

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


class AppInfoTests(unittest.TestCase):
    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "release_workbench", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def make_bundle(self, root: Path, name: str, entries: list[str]) -> Path:
        contents = root / name / "Contents"
        contents.mkdir(parents=True, exist_ok=True)
        for entry in entries:
            path = contents / entry
            path.parent.mkdir(parents=True, exist_ok=True)
            if entry.endswith("/"):
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.touch()
        return root / name

    def write_plist(self, app: Path, value: object) -> None:
        plist_path = app / "Contents" / "Info.plist"
        with plist_path.open("wb") as plist_file:
            plistlib.dump(value, plist_file)  # type: ignore[arg-type]

    def test_valid_bundle(self) -> None:
        import json

        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(
                Path(tmp),
                "Sample.app",
                ["Info.plist", "MacOS/Sample", "Resources/", "Frameworks/"],
            )
            self.write_plist(
                app,
                {"CFBundleIdentifier": "com.example.sample", "CFBundleExecutable": "Sample"},
            )
            result = self.invoke("app-info", str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        report = json.loads(result.stdout)
        self.assertEqual(report["bundle_id"], "com.example.sample")
        self.assertEqual(report["executable"], "Sample")
        self.assertEqual(report["info_plist"], "Contents/Info.plist")
        self.assertEqual(
            report["components"],
            ["Contents/Frameworks", "Contents/Info.plist", "Contents/MacOS", "Contents/Resources"],
        )
        self.assertEqual(report["status"], "ok")
        self.assertEqual(result.stdout.count("\n"), 1)

    def test_missing_info_plist(self) -> None:
        import json

        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(Path(tmp), "Empty.app", ["MacOS/Empty", "Frameworks/"])
            result = self.invoke("app-info", str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIsNone(report["bundle_id"])
        self.assertIsNone(report["executable"])
        self.assertEqual(report["info_plist"], "Contents/Info.plist")
        self.assertEqual(report["components"], ["Contents/Frameworks", "Contents/MacOS"])
        self.assertEqual(report["status"], "missing-info-plist")

    def test_non_string_plist_values_become_null(self) -> None:
        import json

        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(Path(tmp), "Typed.app", ["Info.plist"])
            self.write_plist(
                app,
                {"CFBundleIdentifier": 42, "CFBundleExecutable": ["Sample"]},
            )
            result = self.invoke("app-info", str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIsNone(report["bundle_id"])
        self.assertIsNone(report["executable"])
        self.assertEqual(report["status"], "ok")

    def test_components_are_sorted(self) -> None:
        import json

        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(
                Path(tmp), "Zed.app", ["Zulu/", "Alpha/", "macOS/"]
            )
            result = self.invoke("app-info", str(app))

        report = json.loads(result.stdout)
        self.assertEqual(
            report["components"],
            ["Contents/Alpha", "Contents/Zulu", "Contents/macOS"],
        )

    def assert_diagnostic_error(self, app: str) -> None:
        result = self.invoke("app-info", app)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(result.stdout, "")
        self.assertIn(app, result.stderr)

    def test_nonexistent_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assert_diagnostic_error(str(Path(tmp) / "Nope.app"))

    def test_not_a_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "File.app"
            target.touch()
            self.assert_diagnostic_error(str(target))

    def test_name_without_app_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "Foo"
            (target / "Contents").mkdir(parents=True)
            self.assert_diagnostic_error(str(target))

    def test_missing_contents_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "Bare.app"
            target.mkdir()
            self.assert_diagnostic_error(str(target))

    def test_invalid_plist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(Path(tmp), "Bad.app", ["Info.plist"])
            (app / "Contents" / "Info.plist").write_text("not a plist")
            self.assert_diagnostic_error(str(app))

    def test_plist_root_not_a_dictionary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(Path(tmp), "Array.app", ["Info.plist"])
            self.write_plist(app, ["a", "b"])
            self.assert_diagnostic_error(str(app))

    def test_extra_arguments_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(Path(tmp), "Sample.app", [])
            for arguments in [
                ("app-info", str(app), "--bogus"),
                ("app-info", str(app), "extra"),
                ("app-info",),
            ]:
                with self.subTest(arguments=arguments):
                    result = self.invoke(*arguments)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
