"""Checks for the documented command-line entry point."""

import json
from pathlib import Path
import plistlib
import struct
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


def _write_mach_o(path: Path, magic: int, cputype: int) -> None:
    # ``magic`` is the first four on-disk bytes read big-endian (e.g. 0xCFFAEDFE
    # for a little-endian 64-bit file); header fields are packed in that order.
    little_endian = magic in (0xCFFAEDFE, 0xCEFAEDFE)
    bits64 = magic in (0xFEEDFACF, 0xCFFAEDFE)
    count = 8 if bits64 else 7
    host_magic = 0xFEEDFACF if bits64 else 0xFEEDFACE
    fields = [host_magic, cputype, 0, 2, 0, 0, 0, 0][:count]
    path.write_bytes(struct.pack(("<" if little_endian else ">") + "%dI" % count, *fields))


class CompatReportTests(unittest.TestCase):
    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "release_workbench", "compat-report", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def _bundle(
        self,
        root: Path,
        name: str = "Example.app",
        plist: object | None = None,
        executable_bytes: bytes | None = None,
    ) -> Path:
        app = root / name
        (app / "Contents" / "MacOS").mkdir(parents=True)
        if plist is not None:
            _write_plist(app / "Contents" / "Info.plist", plist)
        if executable_bytes is not None:
            executable_name = None
            if isinstance(plist, dict):
                value = plist.get("CFBundleExecutable")
                if isinstance(value, str):
                    executable_name = value
            (app / "Contents" / "MacOS" / (executable_name or "Example")).write_bytes(
                executable_bytes
            )
        return app

    def _profile(self, root: Path, data: object | bytes) -> Path:
        profile = root / "profile.json"
        if isinstance(data, bytes):
            profile.write_bytes(data)
        else:
            profile.write_text(json.dumps(data), encoding="utf-8")
        return profile

    def test_compatible_arm64(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = self._bundle(
                root,
                plist={
                    "CFBundleIdentifier": "com.example.App",
                    "CFBundleExecutable": "Example",
                    "LSMinimumSystemVersion": "11.0",
                },
                executable_bytes=b"",
            )
            _write_mach_o(app / "Contents" / "MacOS" / "Example", 0xCFFAEDFE, 0x0100000C)
            profile = self._profile(root, {"os_version": "12.0", "cpu": "arm64"})
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

    def test_cpu_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = self._bundle(
                root,
                plist={"CFBundleExecutable": "Example"},
                executable_bytes=b"",
            )
            _write_mach_o(app / "Contents" / "MacOS" / "Example", 0xFEEDFACF, 0x01000007)
            profile = self._profile(root, {"os_version": "13.1", "cpu": "arm64"})
            result = self.invoke(str(app), str(profile))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["architectures"], ["x86_64"])
        self.assertEqual(
            report["blocks"],
            [{"code": "cpu", "detail": {"supported": ["x86_64"], "target": "arm64"}}],
        )
        self.assertEqual(report["status"], "blocked")

    def test_os_version_block_segment_count_tiebreak(self) -> None:
        # Common segments equal -> the version with more segments is greater.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = self._bundle(
                root,
                plist={
                    "CFBundleExecutable": "Example",
                    "LSMinimumSystemVersion": "10.15.0",
                },
                executable_bytes=b"",
            )
            _write_mach_o(app / "Contents" / "MacOS" / "Example", 0xFEEDFACF, 0x01000007)
            profile = self._profile(root, {"os_version": "10.15", "cpu": "x86_64"})
            result = self.invoke(str(app), str(profile))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(
            report["blocks"],
            [
                {
                    "code": "os-version",
                    "detail": {"required": "10.15.0", "target": "10.15"},
                }
            ],
        )
        self.assertEqual(report["status"], "blocked")

    def test_blocks_sorted_by_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = self._bundle(
                root,
                plist={
                    "CFBundleExecutable": "Example",
                    "LSMinimumSystemVersion": "12.0",
                },
                executable_bytes=b"",
            )
            _write_mach_o(app / "Contents" / "MacOS" / "Example", 0xCFFAEDFE, 0x0100000C)
            profile = self._profile(root, {"os_version": "11", "cpu": "x86_64"})
            result = self.invoke(str(app), str(profile))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual([block["code"] for block in report["blocks"]], ["cpu", "os-version"])

    def test_32bit_magics(self) -> None:
        cases = [
            (0xFEEDFACE, 7, "i386"),
            (0xCEFAEDFE, 12, "armv7"),
        ]
        for magic, cputype, expected in cases:
            with self.subTest(magic=hex(magic)):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    app = self._bundle(
                        root,
                        plist={"CFBundleExecutable": "Example"},
                        executable_bytes=b"",
                    )
                    _write_mach_o(
                        app / "Contents" / "MacOS" / "Example", magic, cputype
                    )
                    profile = self._profile(root, {"os_version": "10", "cpu": "x86_64"})
                    result = self.invoke(str(app), str(profile))

                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["architectures"], [expected])
                self.assertTrue(
                    any(block["code"] == "cpu" for block in report["blocks"])
                )

    def test_fat_and_unknown_magic_give_empty_architectures(self) -> None:
        cases = [
            struct.pack(">2I", 0xCAFEBABE, 2) + b"\x00" * 40,
            struct.pack(">2I", 0xBEBAFECA, 2) + b"\x00" * 40,
            b"#! /bin/sh\nnot mach-o\n",
            b"tiny",
        ]
        for index, payload in enumerate(cases):
            with self.subTest(index=index):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    app = self._bundle(
                        root,
                        plist={"CFBundleExecutable": "Example"},
                        executable_bytes=payload,
                    )
                    profile = self._profile(root, {"os_version": "10", "cpu": "arm64"})
                    result = self.invoke(str(app), str(profile))

                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["architectures"], [])
                # No architectures means no cpu block even on a cpu mismatch.
                self.assertFalse(
                    any(block["code"] == "cpu" for block in report["blocks"])
                )
                self.assertEqual(report["status"], "compatible")

    def test_missing_executable_and_bad_minimum_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = self._bundle(
                root,
                plist={
                    "CFBundleExecutable": "Example",
                    "LSMinimumSystemVersion": "10.04",
                },
            )
            profile = self._profile(root, {"os_version": "99", "cpu": "arm64"})
            result = self.invoke(str(app), str(profile))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIsNone(report["required_os_version"])
        self.assertEqual(report["architectures"], [])
        self.assertEqual(report["blocks"], [])
        self.assertEqual(report["status"], "compatible")

    def test_non_string_minimum_version_is_null(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = self._bundle(
                root,
                plist={
                    "CFBundleExecutable": "Example",
                    "LSMinimumSystemVersion": 11,
                },
                executable_bytes=b"",
            )
            _write_mach_o(app / "Contents" / "MacOS" / "Example", 0xCFFAEDFE, 0x0100000C)
            profile = self._profile(root, {"os_version": "10", "cpu": "arm64"})
            result = self.invoke(str(app), str(profile))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIsNone(report["required_os_version"])
        self.assertEqual(report["blocks"], [])

    def test_missing_info_plist_still_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = self._bundle(root)
            profile = self._profile(root, {"os_version": "12", "cpu": "arm64"})
            result = self.invoke(str(app), str(profile))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIsNone(report["bundle_id"])
        self.assertIsNone(report["executable"])
        self.assertIsNone(report["required_os_version"])
        self.assertEqual(report["architectures"], [])
        self.assertEqual(report["status"], "compatible")

    def test_bad_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = self._bundle(root)
            invalid_profiles = {
                "missing.json": None,
                "syntax.json": b"{not json",
                "root.json": b"[1, 2]",
                "extra.json": b'{"os_version": "12.0", "cpu": "arm64", "x": 1}',
                "missingfield.json": b'{"os_version": "12.0"}',
                "badcpu.json": b'{"os_version": "12.0", "cpu": "ppc"}',
                "badver.json": b'{"os_version": "10.04", "cpu": "arm64"}',
                "badutf8.json": b'{"os_version": "1\xff", "cpu": "arm64"}',
            }
            for name, raw in invalid_profiles.items():
                profile = root / name
                if raw is not None:
                    profile.write_bytes(raw)
                with self.subTest(profile=name):
                    result = self.invoke(str(app), str(profile))
                    self.assertEqual(result.returncode, 2, result.stdout)
                    self.assertEqual(result.stdout, "")
                    self.assertIn(name, result.stderr)

    def test_profile_checked_before_app(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = self._profile(root, {"os_version": "12.0", "cpu": "ppc"})
            result = self.invoke(str(root / "Missing.app"), str(profile))

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(profile), result.stderr)
        self.assertNotIn("Missing.app", result.stderr)

    def test_invalid_app_with_valid_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = self._profile(root, {"os_version": "12.0", "cpu": "arm64"})
            app = root / "NotAnApp"
            app.mkdir()
            result = self.invoke(str(app), str(profile))

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(app), result.stderr)


if __name__ == "__main__":
    unittest.main()
