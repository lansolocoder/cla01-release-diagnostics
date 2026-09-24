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

    def make_app(
        self,
        tmp: str,
        name: str = "Demo.app",
        info: object | None = None,
        binary: bool = False,
    ) -> str:
        app = os.path.join(tmp, name)
        contents = os.path.join(app, "Contents")
        macos = os.path.join(contents, "MacOS")
        os.makedirs(macos)
        if info is not None:
            with open(os.path.join(contents, "Info.plist"), "wb") as fh:
                if binary:
                    plistlib.dump(info, fh, fmt=plistlib.FMT_BINARY)
                else:
                    plistlib.dump(info, fh, fmt=plistlib.FMT_XML)
        return app

    def add_executable(self, app: str, name: str, executable: bool = True) -> str:
        path = os.path.join(app, "Contents", "MacOS", name)
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\n")
        if executable:
            os.chmod(path, 0o755)
        return path

    def test_success_xml_plist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_app(tmp, info={
                "CFBundleIdentifier": "com.example.demo",
                "CFBundleName": "Demo",
                "CFBundleShortVersionString": "1.2.3",
                "CFBundleExecutable": "demo",
                "IgnoredKey": "ignored",
            })
            self.add_executable(app, "demo")
            self.add_executable(app, "zebra-helper")
            self.add_executable(app, "asset.bin", executable=False)
            os.mkdir(os.path.join(app, "Contents", "MacOS", "subdir"))

            result = self.invoke("inspect", app)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            report = json.loads(result.stdout)
            self.assertEqual(report, {
                "bundle_path": os.path.realpath(app),
                "bundle_identifier": "com.example.demo",
                "bundle_name": "Demo",
                "short_version": "1.2.3",
                "executable": "demo",
                "executables": ["asset.bin", "demo", "zebra-helper"],
                "issues": [],
            })

    def test_success_binary_plist_and_null_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_app(
                tmp,
                info={
                    "CFBundleIdentifier": 42,
                    "CFBundleName": ["nope"],
                    "CFBundleShortVersionString": {"nope": 1},
                },
                binary=True,
            )
            result = self.invoke("inspect", app)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertIsNone(report["bundle_identifier"])
            self.assertIsNone(report["bundle_name"])
            self.assertIsNone(report["short_version"])
            self.assertIsNone(report["executable"])
            self.assertEqual(report["executables"], [])
            self.assertEqual(report["issues"], [])

    def test_declared_executable_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_app(tmp, info={"CFBundleExecutable": "demo"})
            for label, setup in [
                ("absent", lambda: None),
                ("directory", lambda: os.mkdir(
                    os.path.join(app, "Contents", "MacOS", "demo"))),
                ("not-executable", lambda: self.add_executable(
                    app, "demo", executable=False)),
            ]:
                with self.subTest(case=label):
                    setup()
                    result = self.invoke("inspect", app)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    report = json.loads(result.stdout)
                    self.assertIsNone(report["executable"])
                    self.assertEqual(
                        report["issues"],
                        [{"code": "executable-missing", "path": "demo"}],
                    )
                    os_path = os.path.join(app, "Contents", "MacOS", "demo")
                    if os.path.isdir(os_path):
                        os.rmdir(os_path)
                    elif os.path.exists(os_path):
                        os.chmod(os_path, stat.S_IRUSR | stat.S_IWUSR)
                        os.remove(os_path)

    def assert_failure(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr.strip(), "")

    def test_failure_wrong_argument_count(self) -> None:
        self.assert_failure(self.invoke("inspect"))
        with tempfile.TemporaryDirectory() as tmp:
            self.assert_failure(self.invoke("inspect", tmp, "extra"))

    def test_failure_path_does_not_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assert_failure(self.invoke("inspect", os.path.join(tmp, "Nope.app")))

    def test_failure_not_a_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = os.path.join(tmp, "NotAnApp.app")
            with open(file_path, "w") as fh:
                fh.write("x")
            self.assert_failure(self.invoke("inspect", file_path))

    def test_failure_missing_info_plist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_app(tmp, info={"CFBundleIdentifier": "com.x"})
            os.remove(os.path.join(app, "Contents", "Info.plist"))
            self.assert_failure(self.invoke("inspect", app))

    def test_failure_unparseable_info_plist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_app(tmp)
            with open(os.path.join(app, "Contents", "Info.plist"), "wb") as fh:
                fh.write(b"not a plist at all")
            self.assert_failure(self.invoke("inspect", app))

    def test_failure_top_level_not_dictionary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_app(tmp, info=["a", "b"])
            self.assert_failure(self.invoke("inspect", app))

    def test_failure_missing_or_invalid_macos_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_app(tmp, info={"CFBundleIdentifier": "com.x"})
            os.rmdir(os.path.join(app, "Contents", "MacOS"))
            self.assert_failure(self.invoke("inspect", app))

            with open(os.path.join(app, "Contents", "MacOS"), "w") as fh:
                fh.write("x")
            self.assert_failure(self.invoke("inspect", app))

    def test_bundle_is_not_modified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_app(tmp, info={
                "CFBundleIdentifier": "com.example.demo",
                "CFBundleExecutable": "demo",
            })
            self.add_executable(app, "demo")
            before = {}
            for root, _dirs, files in os.walk(app):
                for name in files:
                    path = os.path.join(root, name)
                    before[path] = (os.stat(path).st_mtime_ns, os.stat(path).st_size)
            result = self.invoke("inspect", app)
            self.assertEqual(result.returncode, 0, result.stderr)
            for path, (mtime_ns, size) in before.items():
                stat_result = os.stat(path)
                self.assertEqual(stat_result.st_mtime_ns, mtime_ns)
                self.assertEqual(stat_result.st_size, size)


if __name__ == "__main__":
    unittest.main()
