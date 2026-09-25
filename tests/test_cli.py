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


X86_64 = 0x01000007
ARM64 = 0x0100000C


def _macho_single(cputype: int, magic: int = 0xFEEDFACF) -> bytes:
    # magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags, reserved
    return struct.pack("<IIIIIIII", magic, cputype, 0, 2, 0, 0, 0, 0)


def _macho_fat(cputypes: list[int], magic: int = 0xCAFEBABE) -> bytes:
    data = struct.pack(">II", magic, len(cputypes))
    if magic == 0xCAFEBABE:
        for index, cputype in enumerate(cputypes):
            data += struct.pack(
                ">iiIII", cputype, 0, 0x4000 + index * 0x4000, 0x1000, 12
            )
    else:
        for index, cputype in enumerate(cputypes):
            data += struct.pack(
                ">iiQQII",
                cputype,
                0,
                0x4000 + index * 0x4000,
                0x1000,
                12,
                0,
            )
    return data


def _build_bundle(
    tmp: str,
    plist: dict,
    executable: bytes | None = None,
    executable_name: str = "App",
) -> Path:
    app = Path(tmp) / "Example.app"
    contents = app / "Contents"
    macos = contents / "MacOS"
    macos.mkdir(parents=True)
    _write_plist(contents / "Info.plist", plist)
    if executable is not None:
        (macos / executable_name).write_bytes(executable)
    return app


class ArchCheckTests(unittest.TestCase):
    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "release_workbench", "arch-check", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_listed_in_help(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "release_workbench", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("arch-check", result.stdout)

    def test_single_arch_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _build_bundle(
                tmp,
                {
                    "CFBundleExecutable": "App",
                    "LSEntry": {"archs": ["arm64"]},
                },
                _macho_single(ARM64),
            )
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "executable": "Contents/MacOS/App",
                "declared_archs": ["arm64"],
                "actual_archs": ["arm64"],
                "match": True,
            },
        )

    def test_arch_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _build_bundle(
                tmp,
                {
                    "CFBundleExecutable": "App",
                    "LSEntry": {"archs": ["arm64"]},
                },
                _macho_single(X86_64),
            )
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["declared_archs"], ["arm64"])
        self.assertEqual(report["actual_archs"], ["x86_64"])
        self.assertFalse(report["match"])

    def test_fat_universal_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _build_bundle(
                tmp,
                {
                    "CFBundleExecutable": "App",
                    "LSEntry": {"archs": ["universal"]},
                },
                _macho_fat([X86_64, ARM64]),
            )
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["actual_archs"], ["arm64", "x86_64"])
        self.assertTrue(report["match"])

    def test_universal_requires_two_archs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _build_bundle(
                tmp,
                {
                    "CFBundleExecutable": "App",
                    "LSEntry": {"archs": ["universal"]},
                },
                _macho_single(ARM64),
            )
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["match"])

    def test_fat64_header_is_read_big_endian(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _build_bundle(
                tmp,
                {
                    "CFBundleExecutable": "App",
                    "LSEntry": {"archs": ["arm64", "x86_64"]},
                },
                _macho_fat([X86_64, ARM64], magic=0xCAFEBABF),
            )
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["match"])

    def test_32bit_magic_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _build_bundle(
                tmp,
                {"CFBundleExecutable": "App"},
                _macho_single(X86_64, magic=0xFEEDFACE),
            )
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["actual_archs"], ["x86_64"])
        self.assertTrue(report["match"])

    def test_missing_lsentry_key_means_empty_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _build_bundle(
                tmp,
                {"CFBundleExecutable": "App"},
                _macho_single(X86_64),
            )
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["declared_archs"], [])
        self.assertTrue(report["match"])

    def test_declared_archs_sorted_and_deduped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _build_bundle(
                tmp,
                {
                    "CFBundleExecutable": "App",
                    "LSEntry": {"archs": ["x86_64", "arm64", "arm64"]},
                },
                _macho_fat([ARM64, ARM64, X86_64]),
            )
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["declared_archs"], ["arm64", "x86_64"])
        self.assertEqual(report["actual_archs"], ["arm64", "x86_64"])
        self.assertTrue(report["match"])

    def test_unknown_cputype_becomes_other_and_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _build_bundle(
                tmp,
                {"CFBundleExecutable": "App"},
                _macho_single(0x00000012),
            )
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["actual_archs"], [])
        self.assertTrue(report["match"])

    def test_invalid_arch_value_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _build_bundle(
                tmp,
                {
                    "CFBundleExecutable": "App",
                    "LSEntry": {"archs": ["ARM64"]},
                },
                _macho_single(ARM64),
            )
            result = self.invoke(str(app))
            plist_path = str(app / "Contents" / "Info.plist")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(plist_path, result.stderr)

    def test_non_string_arch_element_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _build_bundle(
                tmp,
                {
                    "CFBundleExecutable": "App",
                    "LSEntry": {"archs": [7]},
                },
                _macho_single(ARM64),
            )
            result = self.invoke(str(app))
            plist_path = str(app / "Contents" / "Info.plist")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(plist_path, result.stderr)

    def test_bundle_validation_failures(self) -> None:
        result = self.invoke("/nonexistent/Nope.app")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("/nonexistent/Nope.app", result.stderr)

        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "NoContents.app"
            app.mkdir()
            result = self.invoke(str(app))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(app), result.stderr)

    def test_missing_info_plist_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Empty.app"
            (app / "Contents" / "MacOS").mkdir(parents=True)
            result = self.invoke(str(app))
            plist_path = str(app / "Contents" / "Info.plist")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(plist_path, result.stderr)

    def test_invalid_info_plist_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Broken.app"
            contents = app / "Contents"
            (contents / "MacOS").mkdir(parents=True)
            (contents / "Info.plist").write_text("<<< not a plist >>>")
            result = self.invoke(str(app))
            plist_path = str(contents / "Info.plist")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(plist_path, result.stderr)

    def test_missing_cfbundle_executable_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _build_bundle(
                tmp,
                {"LSEntry": {"archs": ["arm64"]}},
                _macho_single(ARM64),
            )
            result = self.invoke(str(app))
            plist_path = str(app / "Contents" / "Info.plist")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(plist_path, result.stderr)

    def test_non_string_cfbundle_executable_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _build_bundle(
                tmp,
                {"CFBundleExecutable": 7},
                _macho_single(ARM64),
            )
            result = self.invoke(str(app))
            plist_path = str(app / "Contents" / "Info.plist")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(plist_path, result.stderr)

    def test_declared_executable_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _build_bundle(
                tmp,
                {"CFBundleExecutable": "Missing"},
            )
            result = self.invoke(str(app))
            exec_path = str(app / "Contents" / "MacOS" / "Missing")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(exec_path, result.stderr)

    def test_declared_executable_is_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Example.app"
            contents = app / "Contents"
            macos = contents / "MacOS"
            (macos / "App").mkdir(parents=True)
            _write_plist(
                contents / "Info.plist", {"CFBundleExecutable": "App"}
            )
            result = self.invoke(str(app))
            exec_path = str(macos / "App")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(exec_path, result.stderr)

    def test_unrecognised_mach_o_magic_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _build_bundle(
                tmp,
                {"CFBundleExecutable": "App"},
                b"\x7fELF not a mach-o file at all",
            )
            result = self.invoke(str(app))
            exec_path = str(app / "Contents" / "MacOS" / "App")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(exec_path, result.stderr)

    def test_extra_option_is_an_error(self) -> None:
        result = self.invoke("Example.app", "--bogus")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("--bogus", result.stderr)


if __name__ == "__main__":
    unittest.main()
