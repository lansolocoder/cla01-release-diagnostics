"""Checks for the verify-signature subcommand."""

import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def make_code_directory(identity: bytes, team: bytes | None) -> bytes:
    """Build a minimal big-endian CodeDirectory blob."""
    version = 0x20200
    header_size = 52
    ident = identity + b"\x00"
    team_string = team + b"\x00" if team is not None else b""
    ident_offset = header_size
    team_offset = ident_offset + len(ident) if team is not None else 0
    length = header_size + len(ident) + len(team_string)
    header = struct.pack(
        ">13I",
        0xFADE0C02,  # magic
        length,
        version,
        0,  # flags
        0,  # hashOffset
        ident_offset,
        0,  # nSpecialSlots
        0,  # nCodeSlots
        0,  # codeLimit
        0,  # hashSize/hashType/platform/pageSize
        0,  # spare2
        0,  # scatterOffset
        team_offset,
    )
    return header + ident + team_string


def make_signature_blob(identity: bytes, team: bytes | None) -> bytes:
    """Build a minimal embedded signature superblob."""
    directory = make_code_directory(identity, team)
    length = 12 + 8 + len(directory)
    return (
        struct.pack(">III", 0xFADE0CC0, length, 1)
        + struct.pack(">II", 0, 20)
        + directory
    )


def make_thin_macho(blob: bytes | None, endian: str = "<") -> bytes:
    """Build a minimal thin 64-bit Mach-O, optionally with a signature."""
    commands = b""
    if blob is not None:
        dataoff = 32 + 16
        commands = struct.pack(endian + "4I", 0x1D, 16, dataoff, len(blob))
    header = struct.pack(
        endian + "8I",
        0xFEEDFACF,  # magic (MH_MAGIC_64 / MH_CIGAM_64 depending on byte order)
        0x01000007,  # cputype
        3,  # cpusubtype
        2,  # filetype
        1 if blob is not None else 0,  # ncmds
        len(commands),  # sizeofcmds
        0,  # flags
        0,  # reserved
    )
    return header + commands + (blob or b"")


def make_fat_macho(slices: list[bytes], endian: str = ">") -> bytes:
    """Build a fat Mach-O wrapping the given thin slices."""
    table_size = 8 + 20 * len(slices)
    arches = b""
    offset = table_size
    for slice_data in slices:
        arches += struct.pack(endian + "5I", 0x01000007, 3, offset, len(slice_data), 0)
        offset += len(slice_data)
    header = struct.pack(endian + "2I", 0xCAFEBABE, len(slices))
    return header + arches + b"".join(slices)


class VerifySignatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.base = Path(self.tempdir.name)

    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "release_workbench", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def make_bundle(self, name: str = "Demo.app") -> Path:
        bundle = self.base / name
        (bundle / "Contents" / "MacOS").mkdir(parents=True)
        return bundle

    def write_executable(self, bundle: Path, data: bytes, name: str = "demo") -> None:
        (bundle / "Contents" / "MacOS" / name).write_bytes(data)

    def test_signed_bundle_is_clean(self) -> None:
        bundle = self.make_bundle()
        blob = make_signature_blob(b"com.example.demo", b"TEAM123")
        self.write_executable(bundle, make_thin_macho(blob))
        (bundle / "Contents" / "Info.plist").write_text("plist", encoding="utf-8")
        result = self.invoke("verify-signature", str(bundle))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        report = json.loads(result.stdout)
        self.assertEqual(report["app"], os.path.realpath(bundle))
        self.assertEqual(report["root"], "Contents")
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["totalChecked"], 2)

    def test_big_endian_and_fat_machos_are_checked(self) -> None:
        bundle = self.make_bundle()
        swapped_blob = make_signature_blob(b"com.example.swapped", None)
        self.write_executable(bundle, make_thin_macho(swapped_blob, endian=">"), name="swapped")
        fat_blob = make_signature_blob(b"com.example.universal", None)
        fat = make_fat_macho(
            [make_thin_macho(fat_blob), make_thin_macho(fat_blob, endian=">")]
        )
        self.write_executable(bundle, fat, name="universal")
        result = self.invoke("verify-signature", str(bundle))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["totalChecked"], 2)

    def test_unsigned_macho_is_exit_1(self) -> None:
        bundle = self.make_bundle()
        self.write_executable(bundle, make_thin_macho(None))
        result = self.invoke("verify-signature", str(bundle))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr, "")
        report = json.loads(result.stdout)
        self.assertEqual(len(report["issues"]), 1)
        issue = report["issues"][0]
        self.assertEqual(issue["path"], "MacOS/demo")
        self.assertEqual(issue["kind"], "unsigned")
        self.assertTrue(issue["detail"])

    def test_identity_mismatch_is_exit_3(self) -> None:
        bundle = self.make_bundle()
        blob = make_signature_blob(b"com.example.other", b"TEAM123")
        self.write_executable(bundle, make_thin_macho(blob))
        result = self.invoke("verify-signature", str(bundle))
        self.assertEqual(result.returncode, 3, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["issues"][0]["kind"], "mismatch")
        self.assertIn("other", report["issues"][0]["detail"])

    def test_team_mismatch_is_exit_1(self) -> None:
        bundle = self.make_bundle()
        blob = make_signature_blob(b"com.example.demo", b"TEAM123")
        self.write_executable(bundle, make_thin_macho(blob))
        result = self.invoke("verify-signature", str(bundle), "--team-id", "OTHER99")
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["issues"][0]["kind"], "team-mismatch")

    def test_matching_team_id_is_clean(self) -> None:
        bundle = self.make_bundle()
        blob = make_signature_blob(b"com.example.demo", b"TEAM123")
        self.write_executable(bundle, make_thin_macho(blob))
        result = self.invoke("verify-signature", str(bundle), "--team-id", "TEAM123")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["issues"], [])

    def test_missing_team_slot_mismatches_when_team_id_given(self) -> None:
        bundle = self.make_bundle()
        blob = make_signature_blob(b"com.example.demo", None)
        self.write_executable(bundle, make_thin_macho(blob))
        result = self.invoke("verify-signature", str(bundle), "--team-id", "TEAM123")
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["issues"][0]["kind"], "team-mismatch")

    def test_mismatch_wins_over_team_mismatch(self) -> None:
        bundle = self.make_bundle()
        blob = make_signature_blob(b"com.example.other", b"WRONG")
        self.write_executable(bundle, make_thin_macho(blob))
        result = self.invoke("verify-signature", str(bundle), "--team-id", "TEAM123")
        self.assertEqual(result.returncode, 3, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(len(report["issues"]), 1)
        self.assertEqual(report["issues"][0]["kind"], "mismatch")

    def test_corrupt_signature_is_metadata_and_exit_3(self) -> None:
        bundle = self.make_bundle()
        blob = b"\xde\xad\xbe\xef" + b"\x00" * 20
        self.write_executable(bundle, make_thin_macho(blob))
        result = self.invoke("verify-signature", str(bundle))
        self.assertEqual(result.returncode, 3, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["issues"][0]["kind"], "metadata")

    def test_missing_identity_slot_is_metadata(self) -> None:
        bundle = self.make_bundle()
        directory = struct.pack(">13I", 0xFADE0C02, 52, 0x20200, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        blob = struct.pack(">III", 0xFADE0CC0, 20 + len(directory), 1)
        blob += struct.pack(">II", 0, 20) + directory
        self.write_executable(bundle, make_thin_macho(blob))
        result = self.invoke("verify-signature", str(bundle))
        self.assertEqual(result.returncode, 3, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["issues"][0]["kind"], "metadata")

    def test_no_macho_is_exit_4(self) -> None:
        bundle = self.make_bundle()
        (bundle / "Contents" / "Info.plist").write_text("plist", encoding="utf-8")
        result = self.invoke("verify-signature", str(bundle))
        self.assertEqual(result.returncode, 4, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["totalChecked"], 1)

    def test_empty_contents_is_exit_4(self) -> None:
        bundle = self.base / "Empty.app"
        (bundle / "Contents").mkdir(parents=True)
        result = self.invoke("verify-signature", str(bundle))
        self.assertEqual(result.returncode, 4, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["totalChecked"], 0)

    def test_issues_are_sorted_by_path(self) -> None:
        bundle = self.make_bundle()
        self.write_executable(bundle, make_thin_macho(None), name="b")
        self.write_executable(bundle, make_thin_macho(None), name="a")
        result = self.invoke("verify-signature", str(bundle))
        self.assertEqual(result.returncode, 1, result.stderr)
        paths = [issue["path"] for issue in json.loads(result.stdout)["issues"]]
        self.assertEqual(paths, ["MacOS/a", "MacOS/b"])

    def test_non_macho_files_produce_no_issues(self) -> None:
        bundle = self.make_bundle()
        (bundle / "Contents" / "Info.plist").write_bytes(b"\xcf\xfa\xed")  # truncated magic
        blob = make_signature_blob(b"com.example.demo", None)
        self.write_executable(bundle, make_thin_macho(blob))
        result = self.invoke("verify-signature", str(bundle))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["totalChecked"], 2)

    def test_missing_path_is_exit_2(self) -> None:
        missing = str(self.base / "Nope.app")
        result = self.invoke("verify-signature", missing)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(missing, result.stderr)

    def test_non_app_directory_is_exit_2(self) -> None:
        target = self.base / "Demo.bundle"
        target.mkdir()
        result = self.invoke("verify-signature", str(target))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(target), result.stderr)

    def test_missing_contents_is_exit_3(self) -> None:
        bundle = self.base / "Empty.app"
        bundle.mkdir()
        result = self.invoke("verify-signature", str(bundle))
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout, "")
        self.assertIn(os.path.join(os.path.realpath(bundle), "Contents"), result.stderr)

    def test_unknown_argument_is_an_error(self) -> None:
        bundle = self.make_bundle()
        result = self.invoke("verify-signature", str(bundle), "--bogus")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_bundle_is_not_modified(self) -> None:
        bundle = self.make_bundle()
        blob = make_signature_blob(b"com.example.demo", b"TEAM123")
        self.write_executable(bundle, make_thin_macho(blob))
        before = {
            str(path): path.read_bytes() for path in sorted(bundle.rglob("*")) if path.is_file()
        }
        result = self.invoke("verify-signature", str(bundle))
        self.assertEqual(result.returncode, 0, result.stderr)
        after = {
            str(path): path.read_bytes() for path in sorted(bundle.rglob("*")) if path.is_file()
        }
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
