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


def thin_macho(cputype: int, magic: int = 0xFEEDFACF, endian: str = "<") -> bytes:
    """Build a minimal mach_header(_64); only cputype is read."""
    return struct.pack(endian + "IiIIIII", magic, cputype, 0, 2, 0, 0, 0)


def fat_macho(
    cpu_types: list[int], magic: int = 0xCAFEBABE
) -> bytes:
    """Build a big-endian fat header with one entry per cputype."""
    header = struct.pack(">II", magic, len(cpu_types))
    if magic == 0xCAFEBABE:
        entries = b"".join(
            struct.pack(">IIIII", cputype, 0, 0x1000 * (i + 1), 0x1000, 0)
            for i, cputype in enumerate(cpu_types)
        )
    else:
        entries = b"".join(
            struct.pack(">IIQQII", cputype, 0, 0x1000 * (i + 1), 0x1000, 0, 0)
            for i, cputype in enumerate(cpu_types)
        )
    return header + entries


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


def _make_app(
    tmp: str,
    *,
    name: str = "Example.app",
    plist: object | None = None,
    executable: str = "Example",
    macho: bytes | None = None,
) -> tuple[Path, Path]:
    """Create a minimal .app bundle and return (app path, plist path)."""
    app = Path(tmp) / name
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    plist_path = contents / "Info.plist"
    _write_plist(plist_path, plist if plist is not None else {})
    if macho is not None:
        (contents / "MacOS" / executable).write_bytes(macho)
    return app, plist_path


class ArchCheckTests(unittest.TestCase):
    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "release_workbench", "arch-check", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_thin_arm64_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, _ = _make_app(
                tmp,
                plist={
                    "CFBundleExecutable": "Example",
                    "LSEntry.archs": ["arm64"],
                },
                macho=thin_macho(0x0100000C),
            )
            result = self.invoke(str(app))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {
                "executable": "Contents/MacOS/Example",
                "declared_archs": ["arm64"],
                "actual_archs": ["arm64"],
                "match": True,
            },
        )
        self.assertEqual(result.stdout.count("\n"), 1)

    def test_thin_x86_64_match_mh_magic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, _ = _make_app(
                tmp,
                plist={
                    "CFBundleExecutable": "Example",
                    "LSEntry.archs": ["x86_64"],
                },
                macho=thin_macho(0x01000007, magic=0xFEEDFACE),
            )
            result = self.invoke(str(app))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["actual_archs"], ["x86_64"]
        )

    def test_big_endian_thin_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, _ = _make_app(
                tmp,
                plist={"CFBundleExecutable": "Example"},
                macho=thin_macho(0x0100000C, endian=">"),
            )
            result = self.invoke(str(app))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["actual_archs"], ["arm64"]
        )

    def test_fat_universal_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, _ = _make_app(
                tmp,
                plist={
                    "CFBundleExecutable": "Example",
                    "LSEntry.archs": ["universal"],
                },
                macho=fat_macho([0x01000007, 0x0100000C]),
            )
            result = self.invoke(str(app))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["declared_archs"], ["universal"])
        self.assertEqual(report["actual_archs"], ["arm64", "x86_64"])
        self.assertIs(report["match"], True)

    def test_fat_64_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, _ = _make_app(
                tmp,
                plist={
                    "CFBundleExecutable": "Example",
                    "LSEntry.archs": ["x86_64", "arm64"],
                },
                macho=fat_macho(
                    [0x0100000C, 0x01000007], magic=0xCAFEBABF
                ),
            )
            result = self.invoke(str(app))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["actual_archs"], ["arm64", "x86_64"])
        self.assertIs(report["match"], True)

    def test_universal_requires_two_slices_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, _ = _make_app(
                tmp,
                plist={
                    "CFBundleExecutable": "Example",
                    "LSEntry.archs": ["universal"],
                },
                macho=fat_macho([0x0100000C]),
            )
            result = self.invoke(str(app))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIs(json.loads(result.stdout)["match"], False)

    def test_declared_arch_absent_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, _ = _make_app(
                tmp,
                plist={
                    "CFBundleExecutable": "Example",
                    "LSEntry.archs": ["x86_64", "arm64"],
                },
                macho=thin_macho(0x0100000C),
            )
            result = self.invoke(str(app))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["actual_archs"], ["arm64"])
        self.assertIs(report["match"], False)

    def test_no_declaration_is_empty_and_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, _ = _make_app(
                tmp,
                plist={"CFBundleExecutable": "Example"},
                macho=thin_macho(0x0100000C),
            )
            result = self.invoke(str(app))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["declared_archs"], [])
        self.assertIs(report["match"], True)

    def test_empty_declaration_array(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, _ = _make_app(
                tmp,
                plist={
                    "CFBundleExecutable": "Example",
                    "LSEntry.archs": [],
                },
                macho=thin_macho(0x01000007),
            )
            result = self.invoke(str(app))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["declared_archs"], [])

    def test_unknown_cputype_becomes_empty_actual(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, _ = _make_app(
                tmp,
                plist={
                    "CFBundleExecutable": "Example",
                    "LSEntry.archs": ["arm64"],
                },
                macho=thin_macho(12),  # e.g. 32-bit arm
            )
            result = self.invoke(str(app))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["actual_archs"], [])
        self.assertIs(report["match"], False)

    def test_declared_archs_sorted_and_deduped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, _ = _make_app(
                tmp,
                plist={
                    "CFBundleExecutable": "Example",
                    "LSEntry.archs": ["x86_64", "arm64", "arm64"],
                },
                macho=fat_macho([0x0100000C, 0x01000007]),
            )
            result = self.invoke(str(app))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["declared_archs"],
            ["arm64", "x86_64"],
        )

    # ---- shared bundle validation errors (exit 2, empty stdout) ----

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

    def test_missing_info_plist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Empty.app"
            (app / "Contents" / "MacOS").mkdir(parents=True)
            result = self.invoke(str(app))
            plist_path = str(app / "Contents" / "Info.plist")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(plist_path, result.stderr)

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

    def test_missing_bundle_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, plist_path = _make_app(tmp, plist={})
            result = self.invoke(str(app))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(plist_path), result.stderr)

    def test_non_string_bundle_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, plist_path = _make_app(
                tmp, plist={"CFBundleExecutable": 7}
            )
            result = self.invoke(str(app))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(plist_path), result.stderr)

    def test_declared_executable_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, _ = _make_app(
                tmp,
                plist={"CFBundleExecutable": "Missing"},
                macho=None,
            )
            result = self.invoke(str(app))
            exec_path = str(app / "Contents" / "MacOS" / "Missing")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(exec_path, result.stderr)

    def test_declared_executable_is_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Dir.app"
            contents = app / "Contents"
            (contents / "MacOS" / "Example").mkdir(parents=True)
            _write_plist(
                contents / "Info.plist", {"CFBundleExecutable": "Example"}
            )
            result = self.invoke(str(app))
            exec_path = str(contents / "MacOS" / "Example")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(exec_path, result.stderr)

    def test_invalid_arch_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, plist_path = _make_app(
                tmp,
                plist={
                    "CFBundleExecutable": "Example",
                    "LSEntry.archs": ["ARM64"],  # must be lowercase
                },
                macho=thin_macho(0x0100000C),
            )
            result = self.invoke(str(app))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(plist_path), result.stderr)

    def test_unknown_arch_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, plist_path = _make_app(
                tmp,
                plist={
                    "CFBundleExecutable": "Example",
                    "LSEntry.archs": ["ppc"],
                },
                macho=thin_macho(0x0100000C),
            )
            result = self.invoke(str(app))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(plist_path), result.stderr)
        self.assertIn("ppc", result.stderr)

    def test_non_string_arch_element(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, plist_path = _make_app(
                tmp,
                plist={
                    "CFBundleExecutable": "Example",
                    "LSEntry.archs": [42],
                },
                macho=thin_macho(0x0100000C),
            )
            result = self.invoke(str(app))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(plist_path), result.stderr)

    def test_archs_not_an_array(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, plist_path = _make_app(
                tmp,
                plist={
                    "CFBundleExecutable": "Example",
                    "LSEntry.archs": "arm64",
                },
                macho=thin_macho(0x0100000C),
            )
            result = self.invoke(str(app))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(plist_path), result.stderr)

    def test_unrecognized_macho_magic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, _ = _make_app(
                tmp,
                plist={"CFBundleExecutable": "Example"},
                macho=b"\x7fELF" + b"\0" * 32,
            )
            result = self.invoke(str(app))
            exec_path = str(app / "Contents" / "MacOS" / "Example")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(exec_path, result.stderr)

    def test_truncated_macho(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, _ = _make_app(
                tmp,
                plist={"CFBundleExecutable": "Example"},
                macho=b"\xfe\xed\xfa",
            )
            result = self.invoke(str(app))
            exec_path = str(app / "Contents" / "MacOS" / "Example")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(exec_path, result.stderr)

    def test_extra_option_is_an_error(self) -> None:
        result = self.invoke("Example.app", "--bogus")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("--bogus", result.stderr)

    def test_does_not_create_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, _ = _make_app(
                tmp,
                plist={
                    "CFBundleExecutable": "Example",
                    "LSEntry.archs": ["arm64"],
                },
                macho=thin_macho(0x0100000C),
            )
            before = sorted(p.relative_to(app) for p in app.rglob("*"))
            result = self.invoke(str(app))
            after = sorted(p.relative_to(app) for p in app.rglob("*"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, after)


class HelpListsArchCheckTests(unittest.TestCase):
    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "release_workbench", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_command_in_top_level_help(self) -> None:
        result = self.invoke("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("app-info", result.stdout)
        self.assertIn("arch-check", result.stdout)

    def test_subcommand_help(self) -> None:
        result = self.invoke("arch-check", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("arch-check", result.stdout)
        self.assertIn(".app", result.stdout)


if __name__ == "__main__":
    unittest.main()
