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


def _dylib_command(
    name: bytes,
    byteorder: str = "little",
    current_version: int = 0,
    compat_version: int = 0,
) -> bytes:
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


def _macho(magic: bytes, commands: list[bytes]) -> bytes:
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

    def test_ok_single_binary(self) -> None:
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
        report = json.loads(result.stdout)
        self.assertEqual(
            report,
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

    def test_multiple_architectures_and_sorting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            (app / "Contents" / "MacOS" / "Main").write_bytes(
                _macho(MAGIC_64_LE, [_dylib_command(b"/usr/lib/libSystem.B.dylib")])
            )
            (app / "Contents" / "Resources" / "Helper").write_bytes(
                _macho(MAGIC_32_BE, [])
            )
            (app / "Contents" / "Resources" / "Tool").write_bytes(
                _macho(MAGIC_32_LE, [])
            )
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["architectures"], ["i386", "ppc", "x86_64"])
        self.assertEqual(
            [entry["path"] for entry in report["binaries"]],
            [
                "Contents/MacOS/Main",
                "Contents/Resources/Helper",
                "Contents/Resources/Tool",
            ],
        )
        self.assertEqual(report["binaries"][1]["bits"], 32)
        self.assertEqual(report["binaries"][1]["endian"], "big")
        self.assertEqual(report["binaries"][1]["dylibs"], [])

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

    def test_symlinks_are_skipped(self) -> None:
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

    def test_no_macho_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            (app / "Contents" / "MacOS" / "script").write_text("#!/bin/sh\n")
            (app / "Contents" / "Resources" / "tiny").write_bytes(b"\xfe\xed")
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout), {"architectures": [], "binaries": []}
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

    def test_dylib_command_size_too_small(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            command = bytearray(_dylib_command(b"/usr/lib/libSystem.B.dylib"))
            command[4:8] = (16).to_bytes(4, "little")
            binary = app / "Contents" / "MacOS" / "Bad"
            binary.write_bytes(_macho(MAGIC_64_LE, [bytes(command[:16])]))
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

    def test_dylib_version_not_zero(self) -> None:
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


class DiffAppsTests(unittest.TestCase):
    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "release_workbench", "diff-apps", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def make_bundle(self, tmp: str, name: str) -> Path:
        app = Path(tmp) / name
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "Resources").mkdir()
        return app

    def test_identical_bundles_have_no_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp, "Same.app")
            contents = app / "Contents"
            _write_plist(
                contents / "Info.plist",
                {"CFBundleIdentifier": "com.example.App", "CFBundleExecutable": "App"},
            )
            (contents / "MacOS" / "App").write_bytes(
                _macho(MAGIC_64_LE, [_dylib_command(b"/usr/lib/libSystem.B.dylib")])
            )
            result = self.invoke(str(app), str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "added_components": [],
                "removed_components": [],
                "bundle_id_changed": False,
                "executable_changed": False,
                "added_architectures": [],
                "removed_architectures": [],
                "added_binaries": [],
                "removed_binaries": [],
                "dylib_changes": [],
            },
        )

    def test_full_diff(self) -> None:
        with tempfile.TemporaryDirectory() as old_tmp, tempfile.TemporaryDirectory() as new_tmp:
            old = self.make_bundle(old_tmp, "App.app")
            (old / "Contents" / "OldDir").mkdir()
            _write_plist(
                old / "Contents" / "Info.plist",
                {"CFBundleIdentifier": "com.example.App", "CFBundleExecutable": "Main"},
            )
            (old / "Contents" / "MacOS" / "Main").write_bytes(
                _macho(
                    MAGIC_64_LE,
                    [_dylib_command(b"/usr/lib/libSystem.B.dylib")],
                )
            )
            (old / "Contents" / "Resources" / "OldHelper").write_bytes(
                _macho(MAGIC_32_BE, [])
            )

            new = self.make_bundle(new_tmp, "App.app")
            (new / "Contents" / "Frameworks").mkdir()
            _write_plist(
                new / "Contents" / "Info.plist",
                {"CFBundleIdentifier": "com.example.App2", "CFBundleExecutable": "Main2"},
            )
            (new / "Contents" / "MacOS" / "Main").write_bytes(
                _macho(
                    MAGIC_64_LE,
                    [
                        _dylib_command(b"/usr/lib/libSystem.B.dylib"),
                        _dylib_command(b"/usr/lib/libz.1.dylib"),
                    ],
                )
            )
            (new / "Contents" / "Resources" / "NewHelper").write_bytes(
                _macho(MAGIC_32_LE, [])
            )
            result = self.invoke(str(old), str(new))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {
                "added_components": ["Contents/Frameworks"],
                "removed_components": ["Contents/OldDir"],
                "bundle_id_changed": True,
                "executable_changed": True,
                "added_architectures": ["i386"],
                "removed_architectures": ["ppc"],
                "added_binaries": ["Contents/Resources/NewHelper"],
                "removed_binaries": ["Contents/Resources/OldHelper"],
                "dylib_changes": [
                    {
                        "path": "Contents/MacOS/Main",
                        "added": ["/usr/lib/libz.1.dylib"],
                        "removed": [],
                    }
                ],
            },
        )

    def test_dylib_added_and_removed_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as old_tmp, tempfile.TemporaryDirectory() as new_tmp:
            old = self.make_bundle(old_tmp, "App.app")
            new = self.make_bundle(new_tmp, "App.app")
            for app, names in (
                (old, [b"/usr/lib/libOld.dylib", b"/usr/lib/libSame.dylib"]),
                (new, [b"/usr/lib/libNew.dylib", b"/usr/lib/libSame.dylib"]),
            ):
                (app / "Contents" / "MacOS" / "App").write_bytes(
                    _macho(MAGIC_64_LE, [_dylib_command(name) for name in names])
                )
            result = self.invoke(str(old), str(new))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(
            report["dylib_changes"],
            [
                {
                    "path": "Contents/MacOS/App",
                    "added": ["/usr/lib/libNew.dylib"],
                    "removed": ["/usr/lib/libOld.dylib"],
                }
            ],
        )

    def test_missing_info_plist_compared_as_null(self) -> None:
        with tempfile.TemporaryDirectory() as old_tmp, tempfile.TemporaryDirectory() as new_tmp:
            old = self.make_bundle(old_tmp, "App.app")
            new = self.make_bundle(new_tmp, "App.app")
            _write_plist(
                new / "Contents" / "Info.plist",
                {"CFBundleIdentifier": "com.example.App", "CFBundleExecutable": "App"},
            )
            both_missing_result = self.invoke(str(old), str(old))
            changed_result = self.invoke(str(old), str(new))

        self.assertEqual(both_missing_result.returncode, 0, both_missing_result.stderr)
        both_report = json.loads(both_missing_result.stdout)
        self.assertFalse(both_report["bundle_id_changed"])
        self.assertFalse(both_report["executable_changed"])

        self.assertEqual(changed_result.returncode, 0, changed_result.stderr)
        changed_report = json.loads(changed_result.stdout)
        self.assertTrue(changed_report["bundle_id_changed"])
        self.assertTrue(changed_report["executable_changed"])

    def assert_diff_error(self, old: str, new: str, *paths: str) -> None:
        result = self.invoke(old, new)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(result.stdout, "")
        for path in paths:
            self.assertIn(path, result.stderr)

    def test_old_bundle_validated_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            valid = self.make_bundle(tmp, "Valid.app")
            self.assert_diff_error(
                "/nonexistent/Old.app", str(valid), "/nonexistent/Old.app"
            )
            self.assert_diff_error(
                str(valid), "/nonexistent/New.app", "/nonexistent/New.app"
            )
            self.assert_diff_error(
                "/nonexistent/First.app",
                "/nonexistent/Second.app",
                "/nonexistent/First.app",
            )

    def test_not_a_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old_file = Path(tmp) / "Old.app"
            old_file.write_text("not a bundle")
            valid = self.make_bundle(tmp, "Valid.app")
            self.assert_diff_error(str(old_file), str(valid), str(old_file))

    def test_invalid_info_plist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            valid = self.make_bundle(tmp, "Valid.app")
            broken = self.make_bundle(tmp, "Broken.app")
            (broken / "Contents" / "Info.plist").write_text("<<< not a plist >>>")
            plist_path = str(broken / "Contents" / "Info.plist")
            self.assert_diff_error(str(valid), str(broken), plist_path)

    def test_malformed_macho(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            valid = self.make_bundle(tmp, "Valid.app")
            broken = self.make_bundle(tmp, "Broken.app")
            bad_binary = broken / "Contents" / "MacOS" / "Bad"
            bad_binary.write_bytes(MAGIC_64_LE + b"\x00" * 4)
            self.assert_diff_error(str(valid), str(broken), str(bad_binary))

    def test_missing_positional_argument(self) -> None:
        result = self.invoke("Only.app")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
