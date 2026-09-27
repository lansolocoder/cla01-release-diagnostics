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


def _cs_blob(magic: int, payload: bytes = b"") -> bytes:
    """Build a generic signature blob (8-byte aligned payload)."""
    total = 8 + len(payload)
    pad = -total % 8
    return struct.pack(">II", magic, total + pad) + payload + b"\0" * pad


def _code_directory(identifier: str, team_id: str | None, version: int = 0x20500) -> bytes:
    """Build a CodeDirectory blob; it is itself a blob with a magic header."""
    strings = identifier.encode() + b"\0"
    if team_id is not None:
        strings += team_id.encode() + b"\0"
    total = 56 + len(strings)
    total += -total % 8
    strings += b"\0" * (total - 56 - (len(strings)))
    hdr = bytearray(56)
    struct.pack_into(">I", hdr, 0, 0xFADE0C02)
    struct.pack_into(">I", hdr, 4, total)
    struct.pack_into(">I", hdr, 8, version)
    struct.pack_into(">I", hdr, 20, 56)
    if team_id is not None:
        struct.pack_into(">I", hdr, 48, 56 + len(identifier.encode()) + 1)
    return bytes(hdr) + strings


def _superblob(slots: list[tuple[int, bytes]]) -> bytes:
    """Build an embedded-signature SuperBlob from (slot_type, blob) pairs."""
    count = len(slots)
    length = 12 + 8 * count + sum(len(blob) for _, blob in slots)
    out = bytearray(struct.pack(">III", 0xFADE0CC0, length, count))
    offset = 12 + 8 * count
    for slot_type, blob in slots:
        out += struct.pack(">II", slot_type, offset)
        offset += len(blob)
    for _, blob in slots:
        out += blob
    return bytes(out)


def _signed_macho(
    slots: list[tuple[int, bytes]],
    *,
    magic: bytes = MAGIC_64_LE,
    extra_commands: bytes = b"",
    sigsize_override: int | None = None,
) -> bytes:
    """Build a Mach-O whose trailing signature region holds ``slots``."""
    signature = _superblob(slots)
    byteorder = "little" if magic in (MAGIC_32_LE, MAGIC_64_LE) else "big"
    command = struct.pack(
        "<IIII" if byteorder == "little" else ">IIII",
        0x1D,
        16,
        0,  # placeholder, fixed below
        sigsize_override if sigsize_override is not None else len(signature),
    )
    binary = _macho(magic, [extra_commands, command] if extra_commands else [command])
    sigoff = len(binary)
    # Patch dataoff (the load command is the last one, at header end + extra).
    cmd_pos = MACHO_HEADER_SIZE[64 if magic in (MAGIC_64_BE, MAGIC_64_LE) else 32]
    cmd_pos += len(extra_commands)
    binary = bytearray(binary)
    binary[cmd_pos + 8 : cmd_pos + 12] = sigoff.to_bytes(4, byteorder)
    return bytes(binary) + signature


MACHO_HEADER_SIZE = {32: 28, 64: 32}


class SignInfoTests(unittest.TestCase):
    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "release_workbench", "sign-info", *arguments],
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

    def test_signed_and_unsigned_binaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            signed = _signed_macho(
                [
                    (0, _code_directory("com.example.App", "ABCD123456")),
                    (0x10000, _cs_blob(0xFADE0B01, b"cms")),
                ]
            )
            (app / "Contents" / "MacOS" / "App").write_bytes(signed)
            (app / "Contents" / "MacOS" / "Helper").write_bytes(
                _macho(MAGIC_64_LE, [])
            )
            (app / "Contents" / "Resources" / "Tool").write_bytes(
                _macho(MAGIC_32_BE, [])
            )
            (app / "Contents" / "Resources" / "notes").write_text("data")
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "binaries": [
                    {
                        "path": "Contents/MacOS/App",
                        "status": "signed",
                        "identifier": "com.example.App",
                        "team_id": "ABCD123456",
                        "entries": ["CodeDirectory", "SignatureSlot"],
                    },
                    {
                        "path": "Contents/MacOS/Helper",
                        "status": "unsigned",
                        "identifier": None,
                        "team_id": None,
                        "entries": [],
                    },
                    {
                        "path": "Contents/Resources/Tool",
                        "status": "unsigned",
                        "identifier": None,
                        "team_id": None,
                        "entries": [],
                    },
                ],
                "unsigned_binaries": [
                    "Contents/MacOS/Helper",
                    "Contents/Resources/Tool",
                ],
            },
        )

    def test_entries_deduplicated_sorted_and_unknown_slots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            binary = _signed_macho(
                [
                    (0, _code_directory("com.example.App", None)),
                    (5, _cs_blob(0xFADE7171, b"ent")),
                    (7, _cs_blob(0xFADE7172, b"der")),
                    (0x1000, _cs_blob(0xFADE0C02, b"alt")),
                    (4242, _cs_blob(0xFADE0001, b"x")),
                    (0x10000, _cs_blob(0xFADE0B01, b"cms")),
                ]
            )
            (app / "Contents" / "MacOS" / "App").write_bytes(binary)
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        entries = json.loads(result.stdout)["binaries"][0]["entries"]
        self.assertEqual(
            entries,
            [
                "AlternateCodeDirectorySlot",
                "CodeDirectory",
                "DEREntitlementsSlot",
                "EntitlementsSlot",
                "SignatureSlot",
                "Slot4242",
            ],
        )

    def test_older_code_directory_without_team_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            binary = _signed_macho(
                [(0, _code_directory("com.example.Legacy", None, version=0x20100))]
            )
            (app / "Contents" / "MacOS" / "Legacy").write_bytes(binary)
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)["binaries"][0]
        self.assertEqual(report["identifier"], "com.example.Legacy")
        self.assertIsNone(report["team_id"])
        self.assertEqual(report["entries"], ["CodeDirectory"])

    def test_datasize_page_rounded_with_padding(self) -> None:
        # Real-world link commands page-round datasize; the tightly packed
        # directory is shorter and the trailing region bytes are padding.
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            signature = _superblob([(0, _code_directory("com.example.App", None))])
            padded_size = 8192
            command = struct.pack(
                "<IIII", 0x1D, 16, 48, padded_size
            )
            macho_part = _macho(MAGIC_64_LE, [command])
            padding = b"\0" * (padded_size - len(signature))
            (app / "Contents" / "MacOS" / "App").write_bytes(
                macho_part + signature + padding
            )
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)["binaries"][0]
        self.assertEqual(report["status"], "signed")
        self.assertEqual(report["identifier"], "com.example.App")

    def test_no_macho_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            (app / "Contents" / "MacOS" / "script").write_text("#!/bin/sh\n")
            result = self.invoke(str(app))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout), {"binaries": [], "unsigned_binaries": []}
        )

    def test_all_unsigned_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            (app / "Contents" / "MacOS" / "A").write_bytes(_macho(MAGIC_64_LE, []))
            (app / "Contents" / "MacOS" / "B").write_bytes(_macho(MAGIC_32_LE, []))
            result = self.invoke(str(app))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(
            report["unsigned_binaries"],
            ["Contents/MacOS/A", "Contents/MacOS/B"],
        )
        self.assertTrue(all(b["status"] == "unsigned" for b in report["binaries"]))

    def test_recurses_and_skips_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            nested = app / "Contents" / "Resources" / "Frameworks" / "Deep"
            nested.mkdir(parents=True)
            (nested / "Lib").write_bytes(
                _signed_macho([(0, _code_directory("com.example.Lib", None))])
            )
            outside = Path(tmp) / "outside"
            outside.write_bytes(_macho(MAGIC_64_LE, []))
            (app / "Contents" / "MacOS" / "Link").symlink_to(outside)
            result = self.invoke(str(app))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(
            [b["path"] for b in report["binaries"]],
            ["Contents/Resources/Frameworks/Deep/Lib"],
        )
        self.assertEqual(report["unsigned_binaries"], [])

    def assert_sign_error(self, app: Path, failing_file: Path) -> None:
        result = self.invoke(str(app))
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(failing_file), result.stderr)

    def test_signature_data_extends_past_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            command = struct.pack("<IIII", 0x1D, 16, 9999, 40)
            binary = app / "Contents" / "MacOS" / "Bad"
            binary.write_bytes(_macho(MAGIC_64_LE, [command]))
            self.assert_sign_error(app, binary)

    def test_bad_directory_magic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            signature = bytearray(
                _superblob([(0, _code_directory("com.example.Bad", None))])
            )
            struct.pack_into(">I", signature, 0, 0xDEADBEEF)
            # Build the file manually so magic can be corrupted.
            command = struct.pack("<IIII", 0x1D, 16, 48, len(signature))
            macho_part = _macho(MAGIC_64_LE, [command])
            bad = app / "Contents" / "MacOS" / "Bad"
            bad.write_bytes(macho_part + bytes(signature))
            self.assert_sign_error(app, bad)

    def test_directory_length_too_large(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            binary = _signed_macho(
                [(0, _code_directory("com.example.Bad", None))],
                sigsize_override=9999,
            )
            bad = app / "Contents" / "MacOS" / "Bad"
            bad.write_bytes(binary)
            self.assert_sign_error(app, bad)

    def test_directory_length_does_not_fill_blobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            signature = bytearray(
                _superblob([(0, _code_directory("com.example.Bad", None))])
            )
            # Claim a length larger than the blobs actually occupy.
            struct.pack_into(">I", signature, 4, len(signature) + 16)
            command = struct.pack("<IIII", 0x1D, 16, 48, len(signature) + 16)
            macho_part = _macho(MAGIC_64_LE, [command])
            bad = app / "Contents" / "MacOS" / "Bad"
            bad.write_bytes(macho_part + bytes(signature) + b"\0" * 16)
            self.assert_sign_error(app, bad)

    def test_slot_offset_out_of_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            signature = bytearray(
                _superblob([(0, _code_directory("com.example.Bad", None))])
            )
            struct.pack_into(">I", signature, 16, 9999)  # first slot blob offset
            command = struct.pack("<IIII", 0x1D, 16, 48, len(signature))
            macho_part = _macho(MAGIC_64_LE, [command])
            bad = app / "Contents" / "MacOS" / "Bad"
            bad.write_bytes(macho_part + bytes(signature))
            self.assert_sign_error(app, bad)

    def test_slot_blob_extends_past_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            signature = bytearray(
                _superblob([(0, _code_directory("com.example.Bad", None))])
            )
            # The sole blob starts at offset 20; corrupt its length field (+4).
            struct.pack_into(">I", signature, 24, 9999)
            command = struct.pack("<IIII", 0x1D, 16, 48, len(signature))
            macho_part = _macho(MAGIC_64_LE, [command])
            bad = app / "Contents" / "MacOS" / "Bad"
            bad.write_bytes(macho_part + bytes(signature))
            self.assert_sign_error(app, bad)

    def test_index_table_extends_past_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            # Claim many entries while providing only a tiny region.
            signature = struct.pack(">III", 0xFADE0CC0, 20, 100)
            command = struct.pack("<IIII", 0x1D, 16, 48, len(signature))
            macho_part = _macho(MAGIC_64_LE, [command])
            bad = app / "Contents" / "MacOS" / "Bad"
            bad.write_bytes(macho_part + signature)
            self.assert_sign_error(app, bad)

    def test_truncated_macho_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            bad = app / "Contents" / "MacOS" / "Short"
            bad.write_bytes(MAGIC_64_LE + b"\x00" * 4)
            self.assert_sign_error(app, bad)

    def test_bundle_validation_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            not_dir = Path(tmp) / "File.app"
            not_dir.write_text("nope")
            no_suffix = Path(tmp) / "Example"
            (no_suffix / "Contents").mkdir(parents=True)
            no_contents = Path(tmp) / "NoContents.app"
            no_contents.mkdir()
            for target, needle in [
                ("/nonexistent/Nope.app", "/nonexistent/Nope.app"),
                (not_dir, str(not_dir)),
                (no_suffix, str(no_suffix)),
                (no_contents, str(no_contents)),
            ]:
                with self.subTest(target=target):
                    result = self.invoke(str(target))
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, "")
                    self.assertIn(needle, result.stderr)

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

    def test_identical_bundles_have_no_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp, "Same.app")
            _write_plist(
                app / "Contents" / "Info.plist",
                {"CFBundleIdentifier": "com.example.App", "CFBundleExecutable": "App"},
            )
            (app / "Contents" / "MacOS" / "App").write_bytes(
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

    def test_all_kinds_of_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = self.make_bundle(tmp, "Old.app")
            new = self.make_bundle(tmp, "New.app")
            _write_plist(
                old / "Contents" / "Info.plist",
                {"CFBundleIdentifier": "com.example.App", "CFBundleExecutable": "App"},
            )
            _write_plist(
                new / "Contents" / "Info.plist",
                {
                    "CFBundleIdentifier": "com.example.App2",
                    "CFBundleExecutable": "App2",
                },
            )
            (old / "Contents" / "Frameworks").mkdir()
            (new / "Contents" / "PlugIns").mkdir()
            (old / "Contents" / "MacOS" / "App").write_bytes(
                _macho(
                    MAGIC_64_LE,
                    [
                        _dylib_command(b"/usr/lib/libSystem.B.dylib"),
                        _dylib_command(b"/usr/lib/libz.1.dylib"),
                    ],
                )
            )
            (new / "Contents" / "MacOS" / "App").write_bytes(
                _macho(
                    MAGIC_64_LE,
                    [
                        _dylib_command(b"/usr/lib/libSystem.B.dylib"),
                        _dylib_command(b"/usr/lib/libc++.1.dylib"),
                    ],
                )
            )
            (old / "Contents" / "Resources" / "OldHelper").write_bytes(
                _macho(MAGIC_32_LE, [])
            )
            (new / "Contents" / "Resources" / "NewHelper").write_bytes(
                _macho(MAGIC_32_BE, [])
            )
            result = self.invoke(str(old), str(new))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["added_components"], ["Contents/PlugIns"])
        self.assertEqual(report["removed_components"], ["Contents/Frameworks"])
        self.assertTrue(report["bundle_id_changed"])
        self.assertTrue(report["executable_changed"])
        self.assertEqual(report["added_architectures"], ["ppc"])
        self.assertEqual(report["removed_architectures"], ["i386"])
        self.assertEqual(
            report["added_binaries"], ["Contents/Resources/NewHelper"]
        )
        self.assertEqual(
            report["removed_binaries"], ["Contents/Resources/OldHelper"]
        )
        self.assertEqual(
            report["dylib_changes"],
            [
                {
                    "path": "Contents/MacOS/App",
                    "added": ["/usr/lib/libc++.1.dylib"],
                    "removed": ["/usr/lib/libz.1.dylib"],
                }
            ],
        )

    def test_dylib_changes_sorted_and_unchanged_binaries_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = self.make_bundle(tmp, "Old.app")
            new = self.make_bundle(tmp, "New.app")
            _write_plist(old / "Contents" / "Info.plist", {})
            _write_plist(new / "Contents" / "Info.plist", {})
            shared = [_dylib_command(b"/usr/lib/libSystem.B.dylib")]
            (old / "Contents" / "MacOS" / "Same").write_bytes(_macho(MAGIC_64_LE, shared))
            (new / "Contents" / "MacOS" / "Same").write_bytes(_macho(MAGIC_64_LE, shared))
            (old / "Contents" / "MacOS" / "A").write_bytes(
                _macho(MAGIC_64_LE, [_dylib_command(b"/usr/lib/libold.dylib")])
            )
            (new / "Contents" / "MacOS" / "A").write_bytes(
                _macho(MAGIC_64_LE, [_dylib_command(b"/usr/lib/libnew.dylib")])
            )
            result = self.invoke(str(old), str(new))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["dylib_changes"],
            [
                {
                    "path": "Contents/MacOS/A",
                    "added": ["/usr/lib/libnew.dylib"],
                    "removed": ["/usr/lib/libold.dylib"],
                }
            ],
        )

    def test_missing_info_plist_compares_as_null(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = self.make_bundle(tmp, "Old.app")
            new = self.make_bundle(tmp, "New.app")
            # Neither has an Info.plist: null == null, no change.
            result = self.invoke(str(old), str(new))
            report = json.loads(result.stdout)
            self.assertFalse(report["bundle_id_changed"])
            self.assertFalse(report["executable_changed"])

            # New gains a bundle identifier: null != string is a change.
            _write_plist(new / "Contents" / "Info.plist", {"CFBundleIdentifier": "x"})
            result = self.invoke(str(old), str(new))
            report = json.loads(result.stdout)
            self.assertTrue(report["bundle_id_changed"])
            self.assertFalse(report["executable_changed"])

    def assert_error(self, old: str, new: str, path: str) -> None:
        result = self.invoke(old, new)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(result.stdout, "")
        self.assertIn(path, result.stderr)

    def test_old_bundle_validated_before_new(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            new = self.make_bundle(tmp, "New.app")
            self.assert_error("/nonexistent/Old.app", str(new), "/nonexistent/Old.app")

            bad_suffix = Path(tmp) / "NoSuffix"
            (bad_suffix / "Contents").mkdir(parents=True)
            self.assert_error(str(bad_suffix), str(new), str(bad_suffix))

            no_contents = Path(tmp) / "NoContents.app"
            no_contents.mkdir()
            self.assert_error(str(no_contents), str(new), str(no_contents))

    def test_new_bundle_validated_after_old(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = self.make_bundle(tmp, "Old.app")
            self.assert_error(str(old), "/nonexistent/New.app", "/nonexistent/New.app")

    def test_invalid_info_plist_in_either_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = self.make_bundle(tmp, "Old.app")
            new = self.make_bundle(tmp, "New.app")
            broken = new / "Contents" / "Info.plist"
            broken.write_text("<<< not a plist >>>")
            result = self.invoke(str(old), str(new))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn(str(broken), result.stderr)

    def test_malformed_macho_in_either_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = self.make_bundle(tmp, "Old.app")
            new = self.make_bundle(tmp, "New.app")
            bad = new / "Contents" / "MacOS" / "Bad"
            bad.write_bytes(MAGIC_64_LE + b"\x00" * 4)
            result = self.invoke(str(old), str(new))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn(str(bad), result.stderr)


if __name__ == "__main__":
    unittest.main()
