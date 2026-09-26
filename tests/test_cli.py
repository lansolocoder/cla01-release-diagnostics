"""Checks for the documented command-line entry point."""

import json
import os
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
        tmp: str,
        name: str,
        plist: object = None,
        files: dict[str, bytes] | None = None,
        dirs: tuple[str, ...] = (),
        symlinks: tuple[tuple[str, str], ...] = (),
        create_plist: bool = False,
    ) -> Path:
        app = Path(tmp) / name
        contents = app / "Contents"
        contents.mkdir(parents=True)
        if create_plist or plist is not None:
            _write_plist(contents / "Info.plist", plist if plist is not None else {})
        for directory in dirs:
            (contents / directory).mkdir()
        for filename, data in (files or {}).items():
            (contents / filename).write_bytes(data)
        for link, target in symlinks:
            (contents / link).symlink_to(target)
        return app

    BASE_PLIST = {
        "CFBundleIdentifier": "com.example.App",
        "CFBundleExecutable": "Example",
        "LSMinimumSystemVersion": "11.0",
    }

    def test_identical_bundles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = self.make_app(
                tmp, "Old.app", self.BASE_PLIST, files={"bin": b"abc"}, dirs=("MacOS",)
            )
            new = self.make_app(
                tmp, "New.app", self.BASE_PLIST, files={"bin": b"abc"}, dirs=("MacOS",)
            )
            result = self.invoke(str(old), str(new))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        report = json.loads(result.stdout)
        self.assertEqual(
            report,
            {"changes": [], "missing_in_new": [], "added_in_new": [], "status": "identical"},
        )
        self.assertEqual(result.stdout.count("\n"), 1)

    def test_full_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = self.make_app(
                tmp,
                "Old.app",
                self.BASE_PLIST,
                files={"gone": b"x", "bin": b"old-long"},
                dirs=("Resources",),
                symlinks=(("link", "bin"),),
            )
            new_plist = dict(self.BASE_PLIST, CFBundleIdentifier="com.example.Other")
            new = self.make_app(
                tmp,
                "New.app",
                new_plist,
                files={"fresh": b"y", "bin": b"new"},
                dirs=("Resources",),
                symlinks=(("link", "bin"),),
            )
            result = self.invoke(str(old), str(new))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["missing_in_new"], ["Contents/gone"])
        self.assertEqual(report["added_in_new"], ["Contents/fresh"])
        self.assertEqual(report["status"], "changed")
        # Directories and symlinks that exist on both sides produce no entry.
        self.assertTrue(
            all(
                change["path"] not in ("Contents/Resources", "Contents/link")
                for change in report["changes"]
            )
        )
        metadata = [c for c in report["changes"] if c["kind"] == "metadata"]
        self.assertEqual(
            metadata,
            [
                {
                    "kind": "metadata",
                    "path": "Contents/Info.plist",
                    "detail": {
                        "key": "CFBundleIdentifier",
                        "old": "com.example.App",
                        "new": "com.example.Other",
                    },
                }
            ],
        )
        modified = [c for c in report["changes"] if c["kind"] == "modified" and c["path"] == "Contents/bin"]
        self.assertEqual(
            modified,
            [{"kind": "modified", "path": "Contents/bin", "detail": {"old_size": 8, "new_size": 3}}],
        )
        # changes sorted by (kind, path).
        keys = [(c["kind"], c["path"]) for c in report["changes"]]
        self.assertEqual(keys, sorted(keys))

    def test_metadata_keys_sorted_with_nulls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = self.make_app(
                tmp,
                "Old.app",
                {"CFBundleIdentifier": "a", "CFBundleExecutable": "X", "LSMinimumSystemVersion": 12},
            )
            new = self.make_app(
                tmp,
                "New.app",
                {"CFBundleIdentifier": "b", "CFBundleExecutable": "Y", "LSMinimumSystemVersion": "13.0"},
            )
            result = self.invoke(str(old), str(new))

        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = [
            change
            for change in json.loads(result.stdout)["changes"]
            if change["kind"] == "metadata"
        ]
        self.assertEqual(
            [(c["detail"]["key"], c["detail"]["old"], c["detail"]["new"]) for c in metadata],
            [
                ("CFBundleExecutable", "X", "Y"),
                ("CFBundleIdentifier", "a", "b"),
                ("LSMinimumSystemVersion", None, "13.0"),
            ],
        )

    def test_invalid_minimum_version_counts_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = self.make_app(tmp, "Old.app", {"LSMinimumSystemVersion": "01.0"})
            new = self.make_app(tmp, "New.app", {"LSMinimumSystemVersion": "1..2"})
            result = self.invoke(str(old), str(new))

        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = [
            c for c in json.loads(result.stdout)["changes"] if c["kind"] == "metadata"
        ]
        self.assertEqual(metadata, [])

    def test_both_missing_key_emits_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = self.make_app(tmp, "Old.app", {"CFBundleIdentifier": "a"})
            new = self.make_app(tmp, "New.app", {"CFBundleIdentifier": "a"})
            result = self.invoke(str(old), str(new))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(
            [c for c in report["changes"] if c["kind"] == "metadata"], []
        )
        self.assertEqual(report["status"], "identical")

    def test_missing_plist_one_skips_metadata_but_lists_component(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = self.make_app(tmp, "Old.app", None)
            new = self.make_app(tmp, "New.app", self.BASE_PLIST)
            result = self.invoke(str(old), str(new))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["added_in_new"], ["Contents/Info.plist"])
        self.assertEqual(
            [c for c in report["changes"] if c["kind"] == "metadata"], []
        )

    def test_invalid_plist_is_exit_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = self.make_app(tmp, "Old.app", self.BASE_PLIST)
            new = self.make_app(tmp, "New.app", None)
            (new / "Contents" / "Info.plist").write_text("<<< not a plist >>>")
            result = self.invoke(str(old), str(new))
            new_plist = str(new / "Contents" / "Info.plist")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(new_plist, result.stderr)
        self.assertIn(str(old), result.stderr)
        self.assertEqual(result.stderr.count("error:"), 1)

    def test_invalid_plist_on_old_side_reports_old_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = self.make_app(tmp, "Old.app", None)
            (old / "Contents" / "Info.plist").write_text("<<< not a plist >>>")
            new = self.make_app(tmp, "New.app", self.BASE_PLIST)
            result = self.invoke(str(old), str(new))
            old_plist = str(old / "Contents" / "Info.plist")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(old_plist, result.stderr)
        self.assertNotIn(str(new), result.stderr)

    def test_new_bundle_invalid_reports_both_paths_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = self.make_app(tmp, "Old.app", self.BASE_PLIST)
            new = Path(tmp) / "Plain.app"
            new.write_text("not a bundle")
            result = self.invoke(str(old), str(new))

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(old), result.stderr)
        self.assertIn(str(new), result.stderr)
        self.assertEqual(result.stderr.count("error:"), 1)

    def test_old_bundle_invalid_follows_app_diagnostic(self) -> None:
        result = self.invoke("/nonexistent/Old.app", "/nonexistent/New.app")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("/nonexistent/Old.app", result.stderr)
        self.assertNotIn("/nonexistent/New.app", result.stderr)

    def test_unreadable_shared_file_produces_no_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = self.make_app(tmp, "Old.app", files={"secret": b"aaaa"})
            new = self.make_app(tmp, "New.app", files={"secret": b"bbbb"})
            (new / "Contents" / "secret").chmod(0o000)
            result = self.invoke(str(old), str(new))
            (new / "Contents" / "secret").chmod(0o644)

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        if os.geteuid() != 0:
            self.assertNotIn("Contents/secret", [c["path"] for c in report["changes"]])
            self.assertEqual(report["status"], "identical")


if __name__ == "__main__":
    unittest.main()
