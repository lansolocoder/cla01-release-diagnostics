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


MAGIC_32_BE = b"\xfe\xed\xfa\xce"
MAGIC_32_LE = b"\xce\xfa\xed\xfe"
MAGIC_64_BE = b"\xfe\xed\xfa\xcf"
MAGIC_64_LE = b"\xcf\xfa\xed\xfe"
MAGICS_ALL = (MAGIC_32_BE, MAGIC_32_LE, MAGIC_64_BE, MAGIC_64_LE)


def _dylib_command(
    name: bytes,
    byteorder: str = "little",
    current_version: int = 0,
    compat_version: int = 0,
) -> bytes:
    """Build a well-formed LC_LOAD_DYLIB load command (versions zeroed)."""
    cmdsize = 24 + len(name) + 1
    cmdsize += -cmdsize % 8
    command = bytearray(cmdsize)
    command[0:4] = (0x0C).to_bytes(4, byteorder)
    command[4:8] = cmdsize.to_bytes(4, byteorder)
    command[8:12] = (24).to_bytes(4, byteorder)
    command[16:20] = current_version.to_bytes(4, byteorder)
    command[20:24] = compat_version.to_bytes(4, byteorder)
    command[24 : 24 + len(name)] = name
    return bytes(command)


def _other_command(cmd: int = 0x80000028, payload: bytes = b"") -> bytes:
    """Build a non-dylib load command (LC_MAIN by default)."""
    cmdsize = 8 + len(payload)
    command = bytearray(cmdsize)
    command[0:4] = cmd.to_bytes(4, "little")
    command[4:8] = cmdsize.to_bytes(4, "little")
    command[8:] = payload
    return bytes(command)


def _macho(magic: bytes, commands: list[bytes]) -> bytes:
    """Build a minimal thin Mach-O image with the given load commands."""
    byteorder = "little" if magic in (MAGIC_32_LE, MAGIC_64_LE) else "big"
    header_size = 32 if magic in (MAGIC_64_BE, MAGIC_64_LE) else 28
    header = bytearray(header_size)
    header[0:4] = magic
    header[16:20] = len(commands).to_bytes(4, byteorder)
    header[20:24] = sum(len(command) for command in commands).to_bytes(4, byteorder)
    return bytes(header) + b"".join(commands)


class MachoInfoTests(unittest.TestCase):
    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "release_workbench", "macho-info", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def make_bundle(self, tmp: str, name: str = "Example.app") -> Path:
        app = Path(tmp) / name
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "Resources").mkdir()
        return app

    def test_ok_single_binary_dedup_and_sort(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            binary = _macho(
                MAGIC_64_LE,
                [
                    _dylib_command(b"/usr/lib/libz.1.dylib"),
                    _dylib_command(b"/usr/lib/libSystem.B.dylib"),
                    _dylib_command(b"/usr/lib/libz.1.dylib"),
                ],
            )
            (app / "Contents" / "MacOS" / "Example").write_bytes(binary)
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "architectures": ["x86_64"],
                "binaries": [
                    {
                        "path": "Contents/MacOS/Example",
                        "bits": 64,
                        "endian": "little",
                        "dylibs": [
                            "/usr/lib/libSystem.B.dylib",
                            "/usr/lib/libz.1.dylib",
                        ],
                    }
                ],
            },
        )

    def test_all_four_magics_map_to_labels(self) -> None:
        expected = {
            MAGIC_32_BE: (32, "big", "ppc"),
            MAGIC_32_LE: (32, "little", "i386"),
            MAGIC_64_BE: (64, "big", "ppc64"),
            MAGIC_64_LE: (64, "little", "x86_64"),
        }
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            names = {}
            for index, magic in enumerate(MAGICS_ALL):
                name = f"Bin{index}"
                names[magic] = name
                byteorder = "big" if magic in (MAGIC_32_BE, MAGIC_64_BE) else "little"
                (app / "Contents" / "MacOS" / name).write_bytes(
                    _macho(
                        magic,
                        [_dylib_command(b"/usr/lib/libSystem.B.dylib", byteorder)],
                    )
                )
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["architectures"], ["i386", "ppc", "ppc64", "x86_64"])
        entries = {
            entry["path"].rsplit("/", 1)[-1]: entry for entry in report["binaries"]
        }
        for magic, (bits, endian, _arch) in expected.items():
            entry = entries[names[magic]]
            self.assertEqual((entry["bits"], entry["endian"]), (bits, endian), magic)
            self.assertEqual(entry["dylibs"], ["/usr/lib/libSystem.B.dylib"], magic)

    def test_binary_sorting_and_arch_dedup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            (app / "Contents" / "Resources" / "Zed").write_bytes(
                _macho(MAGIC_64_LE, [])
            )
            (app / "Contents" / "MacOS" / "Main").write_bytes(
                _macho(MAGIC_64_LE, [])
            )
            (app / "Contents" / "MacOS" / "Second").write_bytes(
                _macho(MAGIC_64_LE, [])
            )
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["architectures"], ["x86_64"])
        self.assertEqual(
            [entry["path"] for entry in report["binaries"]],
            [
                "Contents/MacOS/Main",
                "Contents/MacOS/Second",
                "Contents/Resources/Zed",
            ],
        )

    def test_recurses_into_subdirectories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            nested = app / "Contents" / "Resources" / "Frameworks" / "Deep"
            nested.mkdir(parents=True)
            (nested / "Lib").write_bytes(_macho(MAGIC_64_BE, []))
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["architectures"], ["ppc64"])
        self.assertEqual(
            [entry["path"] for entry in report["binaries"]],
            ["Contents/Resources/Frameworks/Deep/Lib"],
        )

    def test_only_macos_and_resources_are_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            frameworks = app / "Contents" / "Frameworks"
            frameworks.mkdir()
            (frameworks / "Ignored").write_bytes(_macho(MAGIC_64_LE, []))
            (app / "Contents" / "Loose").write_bytes(_macho(MAGIC_64_LE, []))
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout), {"architectures": [], "binaries": []}
        )

    def test_symlinks_are_skipped_and_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            target = Path(tmp) / "outside"
            target.write_bytes(_macho(MAGIC_64_LE, []))
            (app / "Contents" / "MacOS" / "Link").symlink_to(target)
            (app / "Contents" / "Resources" / "DirLink").symlink_to(
                target.parent, target_is_directory=True
            )
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout), {"architectures": [], "binaries": []}
        )

    def test_non_macho_and_fat_headers_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            (app / "Contents" / "MacOS" / "script").write_text("#!/bin/sh\n")
            (app / "Contents" / "MacOS" / "tiny").write_bytes(b"\xcf\xfa")
            (app / "Contents" / "Resources" / "fat").write_bytes(
                b"\xca\xfe\xba\xbe" + b"\x00" * 16
            )
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout), {"architectures": [], "binaries": []}
        )

    def test_missing_scan_directories_are_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Empty.app"
            (app / "Contents").mkdir(parents=True)
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout), {"architectures": [], "binaries": []}
        )

    def test_non_dylib_commands_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            (app / "Contents" / "MacOS" / "Example").write_bytes(
                _macho(
                    MAGIC_64_LE,
                    [
                        _other_command(),
                        _dylib_command(b"/usr/lib/libSystem.B.dylib"),
                    ],
                )
            )
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(
            report["binaries"][0]["dylibs"], ["/usr/lib/libSystem.B.dylib"]
        )

    def test_invalid_info_plist_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            (app / "Contents" / "Info.plist").write_text("<<< not a plist >>>")
            (app / "Contents" / "MacOS" / "Example").write_bytes(
                _macho(MAGIC_64_LE, [])
            )
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)["binaries"]), 1)

    def assert_macho_error(self, app: Path, failing_file: Path) -> None:
        result = self.invoke(str(app))
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(failing_file), result.stderr)

    def test_truncated_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            binary = app / "Contents" / "MacOS" / "Short"
            binary.write_bytes(MAGIC_64_LE + b"\x00" * 4)
            self.assert_macho_error(app, binary)

    def test_truncated_header_thirty_two_bit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            binary = app / "Contents" / "Resources" / "Short"
            binary.write_bytes(MAGIC_32_BE + b"\x00" * 8)
            self.assert_macho_error(app, binary)

    def test_dylib_command_size_smaller_than_dylib_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            command = bytearray(_dylib_command(b"/usr/lib/libSystem.B.dylib"))
            command[4:8] = (16).to_bytes(4, "little")
            binary = app / "Contents" / "MacOS" / "Bad"
            binary.write_bytes(_macho(MAGIC_64_LE, [bytes(command[:16])]))
            self.assert_macho_error(app, binary)

    def test_load_command_size_smaller_than_command_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            command = bytearray(_other_command())
            command[4:8] = (4).to_bytes(4, "little")
            binary = app / "Contents" / "MacOS" / "Bad"
            binary.write_bytes(_macho(MAGIC_64_LE, [bytes(command)]))
            self.assert_macho_error(app, binary)

    def test_load_command_extends_past_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            command = bytearray(_dylib_command(b"/usr/lib/libSystem.B.dylib"))
            command[4:8] = (4096).to_bytes(4, "little")
            binary = app / "Contents" / "MacOS" / "Bad"
            binary.write_bytes(_macho(MAGIC_64_LE, [bytes(command)]))
            self.assert_macho_error(app, binary)

    def test_dylib_name_offset_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            command = bytearray(_dylib_command(b"/usr/lib/libSystem.B.dylib"))
            command[8:12] = (8).to_bytes(4, "little")
            binary = app / "Contents" / "MacOS" / "Bad"
            binary.write_bytes(_macho(MAGIC_64_LE, [bytes(command)]))
            self.assert_macho_error(app, binary)

    def test_dylib_current_version_not_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            binary = app / "Contents" / "MacOS" / "Bad"
            binary.write_bytes(
                _macho(
                    MAGIC_64_LE,
                    [_dylib_command(b"/usr/lib/libSystem.B.dylib", current_version=1)],
                )
            )
            self.assert_macho_error(app, binary)

    def test_dylib_compat_version_not_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            binary = app / "Contents" / "MacOS" / "Bad"
            binary.write_bytes(
                _macho(
                    MAGIC_64_LE,
                    [_dylib_command(b"/usr/lib/libSystem.B.dylib", compat_version=1)],
                )
            )
            self.assert_macho_error(app, binary)

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

    def test_extra_option_is_an_error(self) -> None:
        result = self.invoke("Example.app", "--bogus")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("--bogus", result.stderr)

    def test_extra_positional_is_an_error(self) -> None:
        result = self.invoke("Example.app", "Other.app")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
