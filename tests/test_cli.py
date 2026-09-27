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


LC_CODE_SIGNATURE = 0x1D
CSMAGIC_EMBEDDED_SIGNATURE = 0xFADE0CC0
CSMAGIC_CODEDIRECTORY = 0xFADE0C02


def _cs_blob(magic: int, payload: bytes = b"") -> bytes:
    return struct.pack(">II", magic, 8 + len(payload)) + payload


def _code_directory(
    identifier: str, team_id: str | None = None, version: int = 0x00020400
) -> bytes:
    ident = identifier.encode() + b"\0"
    team = b""
    header_size = 44
    team_offset = 0
    if team_id is not None or version >= 0x00020200:
        header_size = 52
    if team_id is not None:
        team = team_id.encode() + b"\0"
        team_offset = header_size + len(ident)
    total = header_size + len(ident) + len(team)
    cd = bytearray(total)
    struct.pack_into(">I", cd, 0, CSMAGIC_CODEDIRECTORY)
    struct.pack_into(">I", cd, 4, total)
    struct.pack_into(">I", cd, 8, version)
    struct.pack_into(">I", cd, 16, total)  # hashOffset
    struct.pack_into(">I", cd, 20, header_size)  # identOffset
    cd[header_size : header_size + len(ident)] = ident
    if team_id is not None:
        struct.pack_into(">I", cd, 48, team_offset)
        cd[team_offset : team_offset + len(team)] = team
    return bytes(cd)


def _code_directory_with_fields(
    identifier: str, fields: list[tuple[int, int]]
) -> bytes:
    """Build a CodeDirectory whose fixed-offset fields are overridden."""
    cd = bytearray(_code_directory(identifier))
    for offset, value in fields:
        struct.pack_into(">I", cd, offset, value)
    return bytes(cd)


def _superblob(entries: list[tuple[int, bytes]]) -> bytes:
    entries = sorted(entries)
    index_size = 12 + 8 * len(entries)
    index = b""
    blobs = b""
    for slot, blob in entries:
        index += struct.pack(">II", slot, index_size + len(blobs))
        blobs += blob
    return (
        struct.pack(">III", CSMAGIC_EMBEDDED_SIGNATURE, index_size + len(blobs), len(entries))
        + index
        + blobs
    )


def _lc_code_signature(sig_offset: int, sig_size: int) -> bytes:
    return struct.pack("<IIII", LC_CODE_SIGNATURE, 16, sig_offset, sig_size)


def _signed_binary(signature: bytes, sig_size: int | None = None) -> bytes:
    """64-bit little-endian Mach-O carrying ``signature`` right after commands."""
    size = len(signature) if sig_size is None else sig_size
    header = bytearray(32)
    header[0:4] = MAGIC_64_LE
    header[16:20] = (1).to_bytes(4, "little")  # ncmds
    header[20:24] = (16).to_bytes(4, "little")  # sizeofcmds
    # Header (32) + command (16) = 48, which is 16-byte aligned.
    return (
        bytes(header)
        + _lc_code_signature(48, size)
        + signature
        + b"\0" * (size - len(signature))
    )


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

    def assert_sign_error(self, app: Path, failing_file: Path) -> None:
        result = self.invoke(str(app))
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(failing_file), result.stderr)

    def test_signed_and_unsigned_binaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            signed = _superblob(
                [
                    (0, _code_directory("com.example.App", "ABCD123456")),
                    (0x10000, _cs_blob(0xFADE0B01, b"cms")),
                ]
            )
            (app / "Contents" / "MacOS" / "App").write_bytes(_signed_binary(signed))
            (app / "Contents" / "MacOS" / "Tool").write_bytes(_macho(MAGIC_64_LE, []))
            (app / "Contents" / "Resources" / "notes").write_text("not mach-o")
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
                        "path": "Contents/MacOS/Tool",
                        "status": "unsigned",
                        "identifier": None,
                        "team_id": None,
                        "entries": [],
                    },
                ],
                "unsigned_binaries": ["Contents/MacOS/Tool"],
            },
        )

    def test_signed_without_team_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            signature = _superblob(
                [(0, _code_directory("com.example.Helper", version=0x00020100))]
            )
            (app / "Contents" / "MacOS" / "Helper").write_bytes(
                _signed_binary(signature)
            )
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        entry = json.loads(result.stdout)["binaries"][0]
        self.assertEqual(entry["status"], "signed")
        self.assertEqual(entry["identifier"], "com.example.Helper")
        self.assertIsNone(entry["team_id"])
        self.assertEqual(entry["entries"], ["CodeDirectory"])

    def test_entries_deduplicated_and_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            # Slots 0 and 0x1000 are both CodeDirectory slots.
            signature = _superblob(
                [
                    (0x10000, _cs_blob(0xFADE0B01, b"cms")),
                    (0x1000, _code_directory("com.example.App")),
                    (0x5, _cs_blob(0xFADE7171, b"ent")),
                    (0, _code_directory("com.example.App")),
                ]
            )
            (app / "Contents" / "MacOS" / "App").write_bytes(_signed_binary(signature))
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        entry = json.loads(result.stdout)["binaries"][0]
        self.assertEqual(
            entry["entries"],
            ["CodeDirectory", "EntitlementsSlot", "SignatureSlot"],
        )

    def test_reserved_lc_region_larger_than_directory(self) -> None:
        # Real linkedit commands reserve 16-byte-aligned space; the
        # SuperBlob length is smaller than the LC data size.
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            signature = _superblob([(0, _code_directory("com.example.App"))])
            binary = _signed_binary(signature, sig_size=len(signature) + 16)
            (app / "Contents" / "MacOS" / "App").write_bytes(binary)
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["binaries"][0]["status"], "signed"
        )

    def test_recurses_into_subdirectories_and_skips_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            signature = _superblob([(0, _code_directory("com.example.Lib"))])
            nested = app / "Contents" / "Resources" / "Frameworks" / "Deep"
            nested.mkdir(parents=True)
            (nested / "Lib").write_bytes(_signed_binary(signature))
            target = Path(tmp) / "outside"
            target.write_bytes(_signed_binary(signature))
            (app / "Contents" / "MacOS" / "Link").symlink_to(target)
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(
            [entry["path"] for entry in report["binaries"]],
            ["Contents/Resources/Frameworks/Deep/Lib"],
        )
        self.assertEqual(report["unsigned_binaries"], [])

    def test_no_macho_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            (app / "Contents" / "MacOS" / "script").write_text("#!/bin/sh\n")
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"binaries": [], "unsigned_binaries": []},
        )

    def test_all_unsigned_exit_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            (app / "Contents" / "MacOS" / "A").write_bytes(_macho(MAGIC_64_LE, []))
            (app / "Contents" / "Resources" / "B").write_bytes(_macho(MAGIC_32_BE, []))
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(
            report["unsigned_binaries"],
            ["Contents/MacOS/A", "Contents/Resources/B"],
        )
        self.assertTrue(
            all(entry["status"] == "unsigned" for entry in report["binaries"])
        )

    def test_signature_offset_past_end_of_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            binary = app / "Contents" / "MacOS" / "Bad"
            binary.write_bytes(
                _macho(MAGIC_64_LE, [_lc_code_signature(9999, 100)])
            )
            self.assert_sign_error(app, binary)

    def test_signature_size_past_end_of_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            signature = _superblob([(0, _code_directory("com.example.App"))])
            binary = app / "Contents" / "MacOS" / "Bad"
            binary.write_bytes(
                _macho(
                    MAGIC_64_LE,
                    [_lc_code_signature(48, 99999), signature],
                )
            )
            self.assert_sign_error(app, binary)

    def test_directory_magic_is_not_embedded_signature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            signature = bytearray(
                _superblob([(0, _code_directory("com.example.App"))])
            )
            signature[0:4] = b"\x00\x00\x00\x00"
            binary = app / "Contents" / "MacOS" / "Bad"
            binary.write_bytes(_signed_binary(bytes(signature)))
            self.assert_sign_error(app, binary)

    def test_directory_length_does_not_match_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            signature = bytearray(
                _superblob([(0, _code_directory("com.example.App"))])
            )
            struct.pack_into(">I", signature, 4, len(signature) + 100)
            binary = app / "Contents" / "MacOS" / "Bad"
            binary.write_bytes(_signed_binary(bytes(signature)))
            self.assert_sign_error(app, binary)

    def test_index_table_extends_past_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            signature = bytearray(
                _superblob([(0, _code_directory("com.example.App"))])
            )
            struct.pack_into(">I", signature, 8, 100000)
            binary = app / "Contents" / "MacOS" / "Bad"
            binary.write_bytes(_signed_binary(bytes(signature)))
            self.assert_sign_error(app, binary)

    def test_slot_offset_out_of_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            signature = (
                struct.pack(">III", CSMAGIC_EMBEDDED_SIGNATURE, 64, 1)
                + struct.pack(">II", 0x10000, 9999)
                + b"\0" * 36
            )
            binary = app / "Contents" / "MacOS" / "Bad"
            binary.write_bytes(_signed_binary(signature))
            self.assert_sign_error(app, binary)

    def test_slot_extends_past_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            cd = bytearray(_code_directory("com.example.App"))
            struct.pack_into(">I", cd, 4, 99999)  # corrupt slot length
            signature = _superblob([(0, bytes(cd))])
            binary = app / "Contents" / "MacOS" / "Bad"
            binary.write_bytes(_signed_binary(signature))
            self.assert_sign_error(app, binary)

    def test_missing_code_directory_slot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            signature = _superblob(
                [(0x10000, _cs_blob(0xFADE0B01, b"cms"))]
            )
            binary = app / "Contents" / "MacOS" / "Bad"
            binary.write_bytes(_signed_binary(signature))
            self.assert_sign_error(app, binary)

    def test_code_directory_magic_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            signature = _superblob(
                [(0, _cs_blob(0xFADE0C01, b"\0" * 40))]
            )
            binary = app / "Contents" / "MacOS" / "Bad"
            binary.write_bytes(_signed_binary(signature))
            self.assert_sign_error(app, binary)

    def test_identifier_offset_out_of_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            cd = _code_directory_with_fields("com.example.App", [(20, 9999)])
            signature = _superblob([(0, cd)])
            binary = app / "Contents" / "MacOS" / "Bad"
            binary.write_bytes(_signed_binary(signature))
            self.assert_sign_error(app, binary)

    def test_truncated_macho_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            binary = app / "Contents" / "MacOS" / "Short"
            binary.write_bytes(MAGIC_64_LE + b"\x00" * 4)
            self.assert_sign_error(app, binary)

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


class DepCheckTests(unittest.TestCase):
    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "release_workbench", "dep-check", *arguments],
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

    def test_ok_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            present = Path(tmp) / "libpresent.dylib"
            present.write_bytes(b"dylib")
            (app / "Contents" / "MacOS" / "App").write_bytes(
                _macho(
                    MAGIC_64_LE,
                    [
                        _dylib_command(b"/usr/lib/libSystem.B.dylib"),
                        _dylib_command(str(present).encode()),
                    ],
                )
            )
            result = self.invoke(str(app), "x86_64")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.count("\n"), 1)
        report = json.loads(result.stdout)
        self.assertEqual(
            report,
            {
                "target_arch": "x86_64",
                "missing_arch_binaries": [],
                "external_dylibs": sorted(
                    ["/usr/lib/libSystem.B.dylib", str(present)]
                ),
                "missing_dylibs": [],
                "unresolved_dylibs": [],
                "status": "ok",
            },
        )

    def test_missing_deps_status_and_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            # A ppc binary (would mismatch x86_64) plus a missing host dylib:
            # missing-deps must win over arch-mismatch.
            (app / "Contents" / "MacOS" / "Helper").write_bytes(
                _macho(
                    MAGIC_32_BE,
                    [
                        _dylib_command(
                            b"/opt/local/lib/libssl.dylib", byteorder="big"
                        ),
                        _dylib_command(
                            b"/opt/local/lib/libssl.dylib", byteorder="big"
                        ),
                        _dylib_command(
                            b"/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",
                            byteorder="big",
                        ),
                    ],
                )
            )
            result = self.invoke(str(app), "x86_64")

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["missing_dylibs"], ["/opt/local/lib/libssl.dylib"])
        self.assertEqual(
            report["external_dylibs"],
            [
                "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",
                "/opt/local/lib/libssl.dylib",
            ],
        )
        self.assertEqual(
            report["missing_arch_binaries"], ["Contents/MacOS/Helper"]
        )
        self.assertEqual(report["status"], "missing-deps")

    def test_arch_mismatch_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            (app / "Contents" / "MacOS" / "Main").write_bytes(
                _macho(MAGIC_64_LE, [_dylib_command(b"/usr/lib/libSystem.B.dylib")])
            )
            (app / "Contents" / "Resources" / "Helper").write_bytes(
                _macho(MAGIC_32_BE, [])
            )
            result = self.invoke(str(app), "x86_64")

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(
            report["missing_arch_binaries"], ["Contents/Resources/Helper"]
        )
        self.assertEqual(report["status"], "arch-mismatch")

    def test_unresolved_and_bundle_frameworks_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            fw_dir = app / "Contents" / "Frameworks"
            fw_dir.mkdir(parents=True)
            bundled = fw_dir / "libFW.dylib"
            bundled.write_bytes(b"macho")
            (app / "Contents" / "MacOS" / "App").write_bytes(
                _macho(
                    MAGIC_64_LE,
                    [
                        _dylib_command(b"@rpath/libPlug.dylib"),
                        _dylib_command(b"@executable_path/../Frameworks/libX.dylib"),
                        _dylib_command(str(bundled).encode()),
                    ],
                )
            )
            result = self.invoke(str(app), "x86_64")

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(
            report["unresolved_dylibs"],
            ["@executable_path/../Frameworks/libX.dylib", "@rpath/libPlug.dylib"],
        )
        self.assertEqual(report["external_dylibs"], [])
        self.assertEqual(report["missing_dylibs"], [])
        self.assertEqual(report["status"], "ok")

    def test_no_macho_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            (app / "Contents" / "MacOS" / "script").write_text("#!/bin/sh\n")
            for arch in ("i386", "ppc", "x86_64", "ppc64"):
                result = self.invoke(str(app), arch)
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["target_arch"], arch)
                self.assertEqual(
                    report,
                    {
                        "target_arch": arch,
                        "missing_arch_binaries": [],
                        "external_dylibs": [],
                        "missing_dylibs": [],
                        "unresolved_dylibs": [],
                        "status": "ok",
                    },
                )

    def test_invalid_architecture_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            result = self.invoke(str(app), "arm64")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("arm64", result.stderr)

    def test_malformed_macho_fails_with_partial_results_suppressed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            bad = app / "Contents" / "MacOS" / "Bad"
            bad.write_bytes(MAGIC_64_LE + b"\x00" * 4)
            result = self.invoke(str(app), "x86_64")
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(bad), result.stderr)

    def test_bundle_validation_errors(self) -> None:
        result = self.invoke("/nonexistent/Nope.app", "x86_64")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("/nonexistent/Nope.app", result.stderr)

        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "File.app"
            app.write_text("not a bundle")
            result = self.invoke(str(app), "x86_64")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn(str(app), result.stderr)

            bundle = Path(tmp) / "Example"
            (bundle / "Contents").mkdir(parents=True)
            result = self.invoke(str(bundle), "x86_64")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn(str(bundle), result.stderr)

            no_contents = Path(tmp) / "NoContents.app"
            no_contents.mkdir()
            result = self.invoke(str(no_contents), "x86_64")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn(str(no_contents), result.stderr)


class UpdateCheckTests(unittest.TestCase):
    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "release_workbench", "update-check", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def make_bundle(self, tmp: str, plist: object = ..., name: str = "Example.app") -> Path:
        app = Path(tmp) / name
        contents = app / "Contents"
        contents.mkdir(parents=True)
        if plist is not ...:
            _write_plist(contents / "Info.plist", plist)
        return app

    def test_ok_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(
                tmp,
                {
                    "SUFeedURL": "https://updates.example.com/appcast.xml",
                    "CFBundleShortVersionString": "1.2.0",
                },
            )
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.count("\n"), 1)
        report = json.loads(result.stdout)
        self.assertEqual(
            report,
            {
                "channel_url": "https://updates.example.com/appcast.xml",
                "current_version": "1.2.0",
                "url_scheme_ok": True,
                "version_comparable": True,
                "version_segments": [1, 2, 0],
                "status": "ok",
            },
        )

    def test_missing_keys_give_missing_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp, {})
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(
            report,
            {
                "channel_url": None,
                "current_version": None,
                "url_scheme_ok": False,
                "version_comparable": False,
                "version_segments": None,
                "status": "missing-config",
            },
        )

    def test_non_string_values_become_null(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(
                tmp, {"SUFeedURL": 42, "CFBundleShortVersionString": ["1", "2"]}
            )
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIsNone(report["channel_url"])
        self.assertIsNone(report["current_version"])
        self.assertEqual(report["status"], "missing-config")

    def test_bad_url_status(self) -> None:
        for url in (
            "ftp://updates.example.com/appcast.xml",
            "https://",
            "https:///appcast.xml",
            "not-a-url",
            "",
        ):
            with self.subTest(url=url), tempfile.TemporaryDirectory() as tmp:
                app = self.make_bundle(
                    tmp,
                    {
                        "SUFeedURL": url,
                        "CFBundleShortVersionString": "1.2.0",
                    },
                )
                result = self.invoke(str(app))
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["channel_url"], url)
                self.assertFalse(report["url_scheme_ok"])
                self.assertEqual(report["status"], "bad-url")

    def test_http_scheme_and_host_with_port_are_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(
                tmp,
                {
                    "SUFeedURL": "http://updates.example.com:8080/appcast.xml",
                    "CFBundleShortVersionString": "1.2.0",
                },
            )
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["url_scheme_ok"])
        self.assertEqual(report["status"], "ok")

    def test_bad_version_status(self) -> None:
        for version in ("", "1..2", "1.2.x", "1.2.", ".1", "1.2-beta"):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as tmp:
                app = self.make_bundle(
                    tmp,
                    {
                        "SUFeedURL": "https://updates.example.com/appcast.xml",
                        "CFBundleShortVersionString": version,
                    },
                )
                result = self.invoke(str(app))
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["current_version"], version)
                self.assertFalse(report["version_comparable"])
                self.assertIsNone(report["version_segments"])
                self.assertEqual(report["status"], "bad-version")

    def test_version_segments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(
                tmp,
                {
                    "SUFeedURL": "https://updates.example.com/appcast.xml",
                    "CFBundleShortVersionString": "10.04.7",
                },
            )
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["version_comparable"])
        self.assertEqual(report["version_segments"], [10, 4, 7])
        self.assertEqual(report["status"], "ok")

    def test_missing_version_with_valid_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(
                tmp, {"SUFeedURL": "https://updates.example.com/appcast.xml"}
            )
            result = self.invoke(str(app))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIsNone(report["current_version"])
        self.assertFalse(report["version_comparable"])
        self.assertIsNone(report["version_segments"])
        self.assertEqual(report["status"], "bad-version")

    def test_bundle_validation_errors(self) -> None:
        result = self.invoke("/nonexistent/Nope.app")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("/nonexistent/Nope.app", result.stderr)

        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "File.app"
            app.write_text("not a bundle")
            result = self.invoke(str(app))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn(str(app), result.stderr)

            bundle = Path(tmp) / "Example"
            (bundle / "Contents").mkdir(parents=True)
            result = self.invoke(str(bundle))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn(str(bundle), result.stderr)

            no_contents = Path(tmp) / "NoContents.app"
            no_contents.mkdir()
            result = self.invoke(str(no_contents))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn(str(no_contents), result.stderr)

    def test_missing_info_plist_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            plist_file = app / "Contents" / "Info.plist"
            result = self.invoke(str(app))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn(str(plist_file), result.stderr)

    def test_invalid_info_plist_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            broken = app / "Contents" / "Info.plist"
            broken.write_text("<<< not a plist >>>")
            result = self.invoke(str(app))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn(str(broken), result.stderr)

    def test_info_plist_root_not_dictionary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp, ["not", "a", "dict"])
            plist_file = app / "Contents" / "Info.plist"
            result = self.invoke(str(app))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn(str(plist_file), result.stderr)

    def test_extra_option_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp, {})
            result = self.invoke(str(app), "--extra")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
