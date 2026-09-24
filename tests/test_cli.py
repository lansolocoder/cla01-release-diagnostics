"""Checks for the documented command-line entry point."""

import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

EXPECTED_TOP_LEVEL_KEYS = {
    "bundle_path",
    "bundle_identifier",
    "bundle_name",
    "short_version",
    "executable",
    "executables",
    "issues",
}


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
                if arguments == ("--help",):
                    self.assertIn("inspect", result.stdout)
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
            [sys.executable, "-m", "release_workbench", "inspect", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def create_app(self, name: str = "Demo.app") -> tuple[Path, Path, Path]:
        app = self.tmp / name
        macos = app / "Contents" / "MacOS"
        macos.mkdir(parents=True)
        return app, app / "Contents" / "Info.plist", macos

    def write_info(
        self, plist_path: Path, content: object, binary: bool = False
    ) -> None:
        fmt = plistlib.FMT_BINARY if binary else plistlib.FMT_XML
        with open(plist_path, "wb") as plist_file:
            plistlib.dump(content, plist_file, fmt=fmt)

    def make_file(self, path: Path, executable: bool = False) -> None:
        path.write_bytes(b"binary\n")
        if executable:
            os.chmod(path, 0o755)

    def assert_failure(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.count("\n"), 1)
        self.assertTrue(result.stderr.endswith("\n"))

    def test_success_with_xml_plist(self) -> None:
        app, info_plist, macos = self.create_app()
        self.write_info(
            info_plist,
            {
                "CFBundleIdentifier": "com.example.Demo",
                "CFBundleName": "Demo",
                "CFBundleShortVersionString": "1.2.3",
                "CFBundleExecutable": "Demo",
                "IgnoredKey": "ignored",
            },
        )
        self.make_file(macos / "Demo", executable=True)
        self.make_file(macos / "HelperTool", executable=True)
        self.make_file(macos / "Notes.txt")
        (macos / "Helpers").mkdir()
        os.symlink("Demo", macos / "DemoLink")

        result = self.invoke(str(app))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")

        report = json.loads(result.stdout)
        self.assertEqual(set(report.keys()), EXPECTED_TOP_LEVEL_KEYS)
        self.assertEqual(report["bundle_path"], os.path.realpath(app))
        self.assertEqual(report["bundle_identifier"], "com.example.Demo")
        self.assertEqual(report["bundle_name"], "Demo")
        self.assertEqual(report["short_version"], "1.2.3")
        self.assertEqual(report["executable"], "Demo")
        self.assertEqual(
            report["executables"], ["Demo", "HelperTool", "Notes.txt"]
        )
        self.assertEqual(report["issues"], [])

    def test_success_with_binary_plist(self) -> None:
        app, info_plist, macos = self.create_app("Bin.app")
        self.write_info(
            info_plist,
            {
                "CFBundleIdentifier": "com.example.Bin",
                "CFBundleName": "Bin",
                "CFBundleShortVersionString": "9",
                "CFBundleExecutable": "Bin",
            },
            binary=True,
        )
        self.make_file(macos / "Bin", executable=True)

        result = self.invoke(str(app))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["bundle_identifier"], "com.example.Bin")
        self.assertEqual(report["short_version"], "9")
        self.assertEqual(report["executable"], "Bin")
        self.assertEqual(report["executables"], ["Bin"])
        self.assertEqual(report["issues"], [])

    def test_missing_and_non_string_keys_become_null(self) -> None:
        app, info_plist, macos = self.create_app()
        self.write_info(
            info_plist,
            {
                "CFBundleIdentifier": 42,
                "CFBundleName": ["nope"],
                # CFBundleShortVersionString intentionally absent.
            },
        )
        self.make_file(macos / "Something")

        result = self.invoke(str(app))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIsNone(report["bundle_identifier"])
        self.assertIsNone(report["bundle_name"])
        self.assertIsNone(report["short_version"])
        self.assertIsNone(report["executable"])
        self.assertEqual(report["executables"], ["Something"])
        self.assertEqual(report["issues"], [])

    def test_declared_executable_not_executable_is_issue(self) -> None:
        app, info_plist, macos = self.create_app()
        self.write_info(
            info_plist,
            {"CFBundleExecutable": "Demo", "CFBundleName": "Demo"},
        )
        target = macos / "Demo"
        self.make_file(target)
        mode_before = stat.S_IMODE(target.stat().st_mode)

        result = self.invoke(str(app))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIsNone(report["executable"])
        self.assertEqual(
            report["issues"],
            [{"code": "executable-missing", "path": "Demo"}],
        )
        # The bundle must not be modified.
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), mode_before)

    def test_declared_executable_is_directory_is_issue(self) -> None:
        app, info_plist, macos = self.create_app()
        self.write_info(info_plist, {"CFBundleExecutable": "Demo"})
        (macos / "Demo").mkdir()
        self.make_file(macos / "Other", executable=True)

        result = self.invoke(str(app))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIsNone(report["executable"])
        self.assertEqual(
            report["issues"],
            [{"code": "executable-missing", "path": "Demo"}],
        )
        self.assertEqual(report["executables"], ["Other"])

    def test_declared_executable_missing_is_issue(self) -> None:
        app, info_plist, macos = self.create_app()
        self.write_info(info_plist, {"CFBundleExecutable": "Ghost"})
        self.make_file(macos / "Real", executable=True)

        result = self.invoke(str(app))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIsNone(report["executable"])
        self.assertEqual(
            report["issues"],
            [{"code": "executable-missing", "path": "Ghost"}],
        )

    def test_executable_symlink_to_executable_file_is_accepted(self) -> None:
        app, info_plist, macos = self.create_app()
        self.write_info(info_plist, {"CFBundleExecutable": "DemoLink"})
        self.make_file(macos / "Demo", executable=True)
        os.symlink("Demo", macos / "DemoLink")

        result = self.invoke(str(app))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["executable"], "DemoLink")
        self.assertEqual(report["executables"], ["Demo"])
        self.assertEqual(report["issues"], [])

    def test_bundle_path_is_realpath_normalized(self) -> None:
        real_dir = self.tmp / "real"
        real_dir.mkdir()
        app2, info_plist2, _macos = self.create_app("real/Demo.app")
        self.write_info(info_plist2, {"CFBundleName": "Demo"})
        alias = self.tmp / "Alias.app"
        os.symlink(app2, alias)

        result = self.invoke(str(alias))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["bundle_path"], os.path.realpath(app2))

    def test_wrong_argument_count(self) -> None:
        self.assert_failure(self.invoke())
        app, _, _ = self.create_app()
        self.assert_failure(self.invoke(str(app), "extra"))

    def test_option_like_tokens_are_arguments_not_flags(self) -> None:
        # A single option-like token is treated as the path (which does not
        # exist); multiple tokens are an argument-count error. Either way the
        # result is one stderr line, empty stdout, exit code 2.
        self.assert_failure(self.invoke("--bogus"))
        self.assert_failure(self.invoke("--help"))
        self.assert_failure(self.invoke("-x"))

    def test_path_does_not_exist(self) -> None:
        self.assert_failure(self.invoke(str(self.tmp / "Missing.app")))

    def test_path_is_not_a_directory(self) -> None:
        plain_file = self.tmp / "NotAnApp"
        plain_file.write_text("x")
        self.assert_failure(self.invoke(str(plain_file)))

    def test_missing_info_plist(self) -> None:
        app, _info_plist, _macos = self.create_app()
        self.assert_failure(self.invoke(str(app)))

    def test_unparseable_info_plist(self) -> None:
        app, info_plist, _macos = self.create_app()
        info_plist.write_bytes(b"this is not a plist")
        self.assert_failure(self.invoke(str(app)))

    def test_info_plist_top_level_not_dictionary(self) -> None:
        app, info_plist, _macos = self.create_app()
        self.write_info(info_plist, ["not", "a", "dict"])
        self.assert_failure(self.invoke(str(app)))

    def test_missing_macos_directory(self) -> None:
        app, info_plist, macos = self.create_app()
        self.write_info(info_plist, {"CFBundleExecutable": "Demo"})
        macos.rmdir()
        self.assert_failure(self.invoke(str(app)))

    def test_macos_not_a_directory(self) -> None:
        app, info_plist, macos = self.create_app()
        self.write_info(info_plist, {"CFBundleExecutable": "Demo"})
        macos.rmdir()
        macos.write_bytes(b"x")
        self.assert_failure(self.invoke(str(app)))


if __name__ == "__main__":
    unittest.main()
