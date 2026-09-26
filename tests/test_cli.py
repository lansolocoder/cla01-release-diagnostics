"""Checks for the documented command-line entry point."""

from pathlib import Path
import json
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


class InspectAppTests(unittest.TestCase):
    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "release_workbench", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def make_bundle(self, root: Path, name: str = "Sample.app") -> Path:
        bundle = root / name
        (bundle / "Contents").mkdir(parents=True)
        return bundle

    def write_plist(self, bundle: Path, values: dict) -> None:
        with (bundle / "Contents" / "Info.plist").open("wb") as plist_file:
            plistlib.dump(values, plist_file)

    def test_help_lists_command(self) -> None:
        result = self.invoke("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("inspect-app", result.stdout)

    def test_full_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self.make_bundle(root)
            self.write_plist(
                bundle,
                {"CFBundleIdentifier": "com.example.sample", "CFBundleExecutable": "Sample"},
            )
            frameworks = bundle / "Contents" / "Frameworks"
            frameworks.mkdir()
            for framework in ["Zeta.framework", "Alpha.framework"]:
                (frameworks / framework).mkdir()
            (frameworks / "Notes.txt").touch()
            (frameworks / "Loose").mkdir()  # not a .framework or .app
            plugins = bundle / "Contents" / "PlugIns"
            plugins.mkdir()
            (plugins / "Bravo.plugin").mkdir()
            (plugins / "Bravo.plugin.bak").mkdir()
            (plugins / "README").touch()
            nested = frameworks / "Helper.app"
            nested.mkdir()

            result = self.invoke("inspect-app", str(bundle))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            payload = json.loads(result.stdout)
            self.assertEqual(
                payload,
                {
                    "appPath": str(bundle),
                    "bundleIdentifier": "com.example.sample",
                    "executableName": "Sample",
                    "frameworks": ["Alpha", "Zeta"],
                    "plugins": ["Bravo"],
                    "nestedApps": ["Helper"],
                },
            )

    def test_app_path_is_echoed_verbatim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self.make_bundle(Path(tmp))
            self.write_plist(bundle, {})
            verbatim = str(bundle).rstrip("/") + "/"
            result = self.invoke("inspect-app", verbatim)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["appPath"], verbatim)

    def test_missing_directories_and_plist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self.make_bundle(Path(tmp))
            result = self.invoke("inspect-app", str(bundle))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            payload = json.loads(result.stdout)
            self.assertIsNone(payload["bundleIdentifier"])
            self.assertIsNone(payload["executableName"])
            self.assertEqual(payload["frameworks"], [])
            self.assertEqual(payload["plugins"], [])
            self.assertEqual(payload["nestedApps"], [])

    def test_corrupt_plist_yields_nulls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self.make_bundle(Path(tmp))
            (bundle / "Contents" / "Info.plist").write_text("not a plist", encoding="utf-8")
            result = self.invoke("inspect-app", str(bundle))
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertIsNone(payload["bundleIdentifier"])
            self.assertIsNone(payload["executableName"])

    def test_nonexistent_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "Missing.app"
            result = self.invoke("inspect-app", str(missing))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertEqual(json.loads(result.stderr), {"error": "invalid-bundle"})

    def test_directory_without_contents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "Empty.app"
            bundle.mkdir()
            result = self.invoke("inspect-app", str(bundle))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertEqual(json.loads(result.stderr), {"error": "invalid-bundle"})

    def test_path_without_app_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "Thing"
            (plain / "Contents").mkdir(parents=True)
            result = self.invoke("inspect-app", str(plain))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertEqual(json.loads(result.stderr), {"error": "not-app-bundle"})

    def test_app_suffix_on_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "File.app"
            target.touch()
            result = self.invoke("inspect-app", str(target))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertEqual(json.loads(result.stderr), {"error": "invalid-bundle"})

    def test_extra_positional_argument(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self.make_bundle(Path(tmp))
            self.write_plist(bundle, {})
            result = self.invoke("inspect-app", str(bundle), "extra-arg")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn("extra-arg", result.stderr)

    def test_unknown_option(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self.make_bundle(Path(tmp))
            self.write_plist(bundle, {})
            result = self.invoke("inspect-app", str(bundle), "--bogus-flag")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn("--bogus-flag", result.stderr)

    def test_missing_positional_argument(self) -> None:
        result = self.invoke("inspect-app")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("APP_PATH", result.stderr)


if __name__ == "__main__":
    unittest.main()
