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


MACHO_MAGICS = {
    "x86_64": b"\xfe\xed\xfa\xcf",
    "i386": b"\xfe\xed\xfa\xce",
    "arm64": b"\xcf\xfa\xed\xfe",
    "armv7": b"\xce\xfa\xed\xfe",
    "fat": b"\xca\xfe\xba\xbe",
}


class CompatReportTests(unittest.TestCase):
    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "release_workbench", "compat-report", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def make_app(
        self,
        tmp: str,
        plist: object = None,
        magic: bytes | None = None,
        executable_name: str = "Example",
    ) -> Path:
        app = Path(tmp) / "Example.app"
        contents = app / "Contents"
        (contents / "MacOS").mkdir(parents=True)
        if plist is not None:
            _write_plist(contents / "Info.plist", plist)
        if magic is not None:
            (contents / "MacOS" / executable_name).write_bytes(magic + b"\x00" * 28)
        return app

    def make_profile(self, tmp: str, data: object) -> Path:
        profile = Path(tmp) / "profile.json"
        if isinstance(data, str):
            profile.write_text(data, encoding="utf-8")
        else:
            profile.write_text(json.dumps(data), encoding="utf-8")
        return profile

    def good_profile(self, tmp: str) -> Path:
        return self.make_profile(tmp, {"os_version": "12.0", "cpu": "arm64"})

    def test_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_app(
                tmp,
                {
                    "CFBundleIdentifier": "com.example.App",
                    "CFBundleExecutable": "Example",
                    "LSMinimumSystemVersion": "11.0",
                },
                magic=MACHO_MAGICS["arm64"],
            )
            profile = self.good_profile(tmp)
            result = self.invoke(str(app), str(profile))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        report = json.loads(result.stdout)
        self.assertEqual(
            report,
            {
                "bundle_id": "com.example.App",
                "executable": "Example",
                "required_os_version": "11.0",
                "architectures": ["arm64"],
                "blocks": [],
                "status": "compatible",
            },
        )
        self.assertEqual(result.stdout.count("\n"), 1)

    def test_blocked_by_os_and_cpu(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_app(
                tmp,
                {
                    "CFBundleIdentifier": "com.example.App",
                    "CFBundleExecutable": "Example",
                    "LSMinimumSystemVersion": "14.2",
                },
                magic=MACHO_MAGICS["x86_64"],
            )
            profile = self.good_profile(tmp)
            result = self.invoke(str(app), str(profile))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(
            report["blocks"],
            [
                {
                    "code": "cpu",
                    "detail": {"supported": ["x86_64"], "target": "arm64"},
                },
                {
                    "code": "os-version",
                    "detail": {"required": "14.2", "target": "12.0"},
                },
            ],
        )

    def test_version_compare_segment_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_app(
                tmp,
                {
                    "CFBundleExecutable": "Example",
                    "LSMinimumSystemVersion": "12.0.0",
                },
                magic=MACHO_MAGICS["arm64"],
            )
            profile = self.good_profile(tmp)
            result = self.invoke(str(app), str(profile))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(
            report["blocks"],
            [
                {
                    "code": "os-version",
                    "detail": {"required": "12.0.0", "target": "12.0"},
                }
            ],
        )

    def test_numeric_segment_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_app(
                tmp,
                {
                    "CFBundleExecutable": "Example",
                    "LSMinimumSystemVersion": "9.9",
                },
                magic=MACHO_MAGICS["arm64"],
            )
            profile = self.make_profile(tmp, {"os_version": "12.0", "cpu": "arm64"})
            result = self.invoke(str(app), str(profile))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "compatible")

    def test_missing_minimum_version_and_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_app(tmp, {"CFBundleExecutable": "Example"})
            profile = self.good_profile(tmp)
            result = self.invoke(str(app), str(profile))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIsNone(report["required_os_version"])
        self.assertEqual(report["architectures"], [])
        self.assertEqual(report["blocks"], [])
        self.assertEqual(report["status"], "compatible")

    def test_invalid_minimum_version_becomes_null(self) -> None:
        for minimum in ["10.13.4 beta", "01.0", "1..2", 13]:
            with self.subTest(minimum=minimum):
                with tempfile.TemporaryDirectory() as tmp:
                    app = self.make_app(
                        tmp,
                        {
                            "CFBundleExecutable": "Example",
                            "LSMinimumSystemVersion": minimum,
                        },
                        magic=MACHO_MAGICS["arm64"],
                    )
                    profile = self.good_profile(tmp)
                    result = self.invoke(str(app), str(profile))

                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertIsNone(report["required_os_version"])

    def test_fat_and_unknown_magic_yield_no_architectures(self) -> None:
        for magic in [MACHO_MAGICS["fat"], b"\xbe\xba\xfe\xca", b"\x7fELF"]:
            with self.subTest(magic=magic):
                with tempfile.TemporaryDirectory() as tmp:
                    app = self.make_app(
                        tmp,
                        {"CFBundleExecutable": "Example"},
                        magic=magic,
                    )
                    profile = self.good_profile(tmp)
                    result = self.invoke(str(app), str(profile))

                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["architectures"], [])
                self.assertEqual(report["status"], "compatible")

    def test_thin_magic_architectures(self) -> None:
        expected = {
            "x86_64": ["x86_64"],
            "i386": ["i386"],
            "arm64": ["arm64"],
            "armv7": ["armv7"],
        }
        for key, archs in expected.items():
            with self.subTest(key=key):
                with tempfile.TemporaryDirectory() as tmp:
                    app = self.make_app(
                        tmp,
                        {"CFBundleExecutable": "Example"},
                        magic=MACHO_MAGICS[key],
                    )
                    profile = self.make_profile(
                        tmp, {"os_version": "12.0", "cpu": "x86_64"}
                    )
                    result = self.invoke(str(app), str(profile))

                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["architectures"], archs)

    def test_missing_info_plist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_app(tmp)
            profile = self.good_profile(tmp)
            result = self.invoke(str(app), str(profile))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIsNone(report["bundle_id"])
        self.assertIsNone(report["executable"])
        self.assertIsNone(report["required_os_version"])
        self.assertEqual(report["architectures"], [])
        self.assertEqual(report["status"], "compatible")

    def test_profile_errors(self) -> None:
        cases = {
            "missing": None,
            "invalid_json": "{not json",
            "root_array": [1, 2],
            "bad_os_version": {"os_version": "12.0.0.1a", "cpu": "arm64"},
            "leading_zero": {"os_version": "012.0", "cpu": "arm64"},
            "os_version_not_string": {"os_version": 12, "cpu": "arm64"},
            "bad_cpu": {"os_version": "12.0", "cpu": "aarch64"},
            "missing_cpu": {"os_version": "12.0"},
            "extra_field": {"os_version": "12.0", "cpu": "arm64", "gpu": "m1"},
        }
        for name, data in cases.items():
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    app = self.make_app(tmp, {"CFBundleExecutable": "Example"})
                    if data is None:
                        profile = Path(tmp) / "absent.json"
                    else:
                        profile = self.make_profile(tmp, data)
                    result = self.invoke(str(app), str(profile))

                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual(result.stdout, "")
                self.assertIn(str(profile), result.stderr)

    def test_profile_checked_before_app(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = self.make_profile(tmp, "{broken")
            result = self.invoke("/nonexistent/Nope.app", str(profile))

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(profile), result.stderr)
        self.assertNotIn("Nope.app", result.stderr)

    def test_app_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = self.good_profile(tmp)
            missing = self.invoke("/nonexistent/Nope.app", str(profile))
            plain = Path(tmp) / "Plain.app"
            plain.write_text("not a bundle")
            not_dir = self.invoke(str(plain), str(profile))
            no_suffix = Path(tmp) / "NoSuffix"
            (no_suffix / "Contents").mkdir(parents=True)
            wrong_name = self.invoke(str(no_suffix), str(profile))
            empty = Path(tmp) / "Empty.app"
            empty.mkdir()
            no_contents = self.invoke(str(empty), str(profile))
            broken = Path(tmp) / "Broken.app"
            (broken / "Contents").mkdir(parents=True)
            (broken / "Contents" / "Info.plist").write_text("<<< not a plist >>>")
            bad_plist = self.invoke(str(broken), str(profile))

        for result in [missing, not_dir, wrong_name, no_contents, bad_plist]:
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertEqual(result.stdout, "")
            self.assertNotEqual(result.stderr, "")


class ReleaseDiffTests(unittest.TestCase):
    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "release_workbench", "release-diff", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def make_app(
        self,
        directory: Path,
        name: str,
        plist: object = None,
        files: dict[str, bytes] | None = None,
        subdirs: tuple[str, ...] = (),
    ) -> Path:
        app = directory / name
        contents = app / "Contents"
        contents.mkdir(parents=True)
        if plist is not None:
            _write_plist(contents / "Info.plist", plist)
        for subdir in subdirs:
            (contents / subdir).mkdir()
        for relative, data in (files or {}).items():
            target = contents / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return app

    def test_identical_bundles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plist = {
                "CFBundleIdentifier": "com.example.App",
                "CFBundleExecutable": "Example",
                "LSMinimumSystemVersion": "11.0",
            }
            old = self.make_app(
                root, "Old.app", plist, {"MacOS/Example": b"\xcf\xfa\xed\xfe"}
            )
            new = self.make_app(
                root, "New.app", plist, {"MacOS/Example": b"\xcf\xfa\xed\xfe"}
            )
            result = self.invoke(str(old), str(new))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        report = json.loads(result.stdout)
        self.assertEqual(
            report,
            {
                "changes": [],
                "missing_in_new": [],
                "added_in_new": [],
                "status": "identical",
            },
        )
        self.assertEqual(result.stdout.count("\n"), 1)

    def test_metadata_modified_and_component_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self.make_app(
                root,
                "Old.app",
                {
                    "CFBundleIdentifier": "com.example.Old",
                    "CFBundleExecutable": "Example",
                    "LSMinimumSystemVersion": "11.0",
                },
                {"MacOS": b"", "Old.txt": b"old", "Shared.txt": b"v1"},
            )
            new = self.make_app(
                root,
                "New.app",
                {
                    "CFBundleIdentifier": "com.example.Old",
                    "CFBundleExecutable": "Example2",
                    "LSMinimumSystemVersion": 13,
                },
                {"MacOS": b"", "New.txt": b"new", "Shared.txt": b"v2!!"},
            )
            result = self.invoke(str(old), str(new))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(
            report["changes"],
            [
                {
                    "kind": "metadata",
                    "path": "Contents/Info.plist",
                    "detail": {
                        "key": "CFBundleExecutable",
                        "old": "Example",
                        "new": "Example2",
                    },
                },
                {
                    "kind": "metadata",
                    "path": "Contents/Info.plist",
                    "detail": {
                        "key": "LSMinimumSystemVersion",
                        "old": "11.0",
                        "new": None,
                    },
                },
                {
                    "kind": "modified",
                    "path": "Contents/Info.plist",
                    "detail": {
                        "old_size": len(
                            plistlib.dumps(
                                {
                                    "CFBundleIdentifier": "com.example.Old",
                                    "CFBundleExecutable": "Example",
                                    "LSMinimumSystemVersion": "11.0",
                                },
                                fmt=plistlib.FMT_XML,
                            )
                        ),
                        "new_size": len(
                            plistlib.dumps(
                                {
                                    "CFBundleIdentifier": "com.example.Old",
                                    "CFBundleExecutable": "Example2",
                                    "LSMinimumSystemVersion": 13,
                                },
                                fmt=plistlib.FMT_XML,
                            )
                        ),
                    },
                },
                {
                    "kind": "modified",
                    "path": "Contents/Shared.txt",
                    "detail": {"old_size": 2, "new_size": 4},
                },
            ],
        )
        self.assertEqual(report["missing_in_new"], ["Contents/Old.txt"])
        self.assertEqual(report["added_in_new"], ["Contents/New.txt"])
        self.assertEqual(report["status"], "changed")

    def test_directories_and_symlinks_not_compared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self.make_app(root, "Old.app", {}, subdirs=("MacOS",))
            new = self.make_app(root, "New.app", {}, subdirs=("MacOS",))
            (old / "Contents" / "Link").symlink_to("MacOS")
            (new / "Contents" / "Link").symlink_to("Resources")
            result = self.invoke(str(old), str(new))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["changes"], [])
        self.assertEqual(report["status"], "identical")

    def test_metadata_skipped_when_info_plist_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self.make_app(root, "Old.app", {"CFBundleExecutable": "Example"})
            new = self.make_app(root, "New.app")
            result = self.invoke(str(old), str(new))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["changes"], [])
        self.assertEqual(report["missing_in_new"], ["Contents/Info.plist"])
        self.assertEqual(report["added_in_new"], [])
        self.assertEqual(report["status"], "changed")

    def test_old_bundle_error_reports_old_path_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            new = self.make_app(Path(tmp), "New.app", {})
            result = self.invoke("/nonexistent/Old.app", str(new))

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("/nonexistent/Old.app", result.stderr)
        self.assertNotIn(str(new), result.stderr)

    def test_new_bundle_error_reports_both_paths_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self.make_app(root, "Old.app", {})
            missing_new = str(root / "Nope.app")
            result = self.invoke(str(old), missing_new)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(old), result.stderr)
        self.assertIn(missing_new, result.stderr)
        self.assertEqual(result.stderr.count("error:"), 1)

    def test_new_invalid_info_plist_reports_both_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self.make_app(root, "Old.app", {})
            new = self.make_app(root, "New.app")
            (new / "Contents" / "Info.plist").write_text("<<< not a plist >>>")
            result = self.invoke(str(old), str(new))

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(old), result.stderr)
        self.assertIn(str(new), result.stderr)

    def test_old_invalid_info_plist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self.make_app(root, "Old.app")
            (old / "Contents" / "Info.plist").write_text("<<< not a plist >>>")
            new = self.make_app(root, "New.app", {})
            result = self.invoke(str(old), str(new))

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(old), result.stderr)


if __name__ == "__main__":
    unittest.main()
