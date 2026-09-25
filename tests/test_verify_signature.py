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


def code_directory(identity: bytes, team: bytes | None, version: int = 0x20200) -> bytes:
    ident = identity + b"\x00"
    team_blob = team + b"\x00" if team is not None else b""
    ident_offset = 52
    team_offset = 52 + len(ident) if team is not None else 0
    length = 52 + len(ident) + len(team_blob)
    header = struct.pack(
        ">IIIIIIIII4BIII",
        0xFADE0C02,  # magic
        length,
        version,
        0,  # flags
        0,  # hashOffset
        ident_offset,
        0,  # nSpecialSlots
        0,  # nCodeSlots
        0,  # codeLimit
        0,
        0,
        0,
        0,  # hashSize, hashType, platform, pageSize
        0,  # spare2
        0,  # scatterOffset
        team_offset,
    )
    return header + ident + team_blob


def superblob(*blobs: bytes, magic: int = 0xFADE0CC0) -> bytes:
    count = len(blobs)
    length = 12 + 8 * count + sum(len(blob) for blob in blobs)
    parts = [struct.pack(">III", magic, length, count)]
    offset = 12 + 8 * count
    for slot, blob in enumerate(blobs):
        parts.append(struct.pack(">II", slot, offset))
        offset += len(blob)
    parts.extend(blobs)
    return b"".join(parts)


def thin_macho(signature: bytes | None = None, endian: str = "<") -> bytes:
    if signature is None:
        return struct.pack(endian + "IIIIIIII", 0xFEEDFACF, 0x01000007, 3, 1, 0, 0, 0, 0)
    header_size = 32
    command_size = 16
    signature_offset = header_size + command_size
    header = struct.pack(endian + "IIIIIIII", 0xFEEDFACF, 0x01000007, 3, 1, 1, command_size, 0, 0)
    command = struct.pack(endian + "IIII", 0x1D, command_size, signature_offset, len(signature))
    return header + command + signature


def fat_macho(*slices: bytes, endian: str = ">") -> bytes:
    count = len(slices)
    header_size = 8 + 20 * count
    parts = [struct.pack(endian + "II", 0xCAFEBABE, count)]
    offset = header_size
    for slice_data in slices:
        parts.append(struct.pack(endian + "IIIII", 0x01000007, 3, offset, len(slice_data), 0))
        offset += len(slice_data)
    parts.extend(slices)
    return b"".join(parts)


def signed_macho(identity: bytes, team: bytes | None = None) -> bytes:
    return thin_macho(superblob(code_directory(identity, team)))


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
        contents = bundle / "Contents"
        (contents / "MacOS").mkdir(parents=True)
        (contents / "Resources").mkdir(parents=True)
        return bundle

    def write(self, bundle: Path, relative: str, data: bytes) -> None:
        (bundle / "Contents" / relative).write_bytes(data)

    def run_verify(self, bundle: Path, *extra: str) -> tuple[int, dict]:
        result = self.invoke("verify-signature", str(bundle), *extra)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        report = json.loads(result.stdout)
        self.assertEqual(report["app"], os.path.realpath(bundle))
        self.assertEqual(report["root"], "Contents")
        return result.returncode, report

    def test_signed_bundle_is_clean(self) -> None:
        bundle = self.make_bundle()
        self.write(bundle, "MacOS/demo", signed_macho(b"com.example.demo", b"TEAM123"))
        self.write(bundle, "Resources/data.bin", b"plain data")
        os.symlink("MacOS/demo", bundle / "Contents" / "runner")
        returncode, report = self.run_verify(bundle, "--team-id", "TEAM123")
        self.assertEqual(returncode, 0)
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["totalChecked"], 2)

    def test_signed_bundle_without_team_option(self) -> None:
        bundle = self.make_bundle()
        self.write(bundle, "MacOS/demo", signed_macho(b"com.example.demo", b"OTHER99"))
        returncode, report = self.run_verify(bundle)
        self.assertEqual(returncode, 0)
        self.assertEqual(report["issues"], [])

    def test_unsigned_macho_is_exit_1(self) -> None:
        bundle = self.make_bundle()
        self.write(bundle, "MacOS/demo", thin_macho())
        returncode, report = self.run_verify(bundle)
        self.assertEqual(returncode, 1)
        self.assertEqual(len(report["issues"]), 1)
        issue = report["issues"][0]
        self.assertEqual(issue["path"], "MacOS/demo")
        self.assertEqual(issue["kind"], "unsigned")
        self.assertTrue(issue["detail"])

    def test_identity_mismatch_is_exit_3(self) -> None:
        bundle = self.make_bundle()
        self.write(bundle, "MacOS/demo", signed_macho(b"com.example.other"))
        returncode, report = self.run_verify(bundle)
        self.assertEqual(returncode, 3)
        self.assertEqual([issue["kind"] for issue in report["issues"]], ["mismatch"])

    def test_team_mismatch_is_exit_1(self) -> None:
        bundle = self.make_bundle()
        self.write(bundle, "MacOS/demo", signed_macho(b"com.example.demo", b"TEAM123"))
        returncode, report = self.run_verify(bundle, "--team-id", "OTHER99")
        self.assertEqual(returncode, 1)
        self.assertEqual([issue["kind"] for issue in report["issues"]], ["team-mismatch"])

    def test_missing_team_slot_counts_as_empty(self) -> None:
        bundle = self.make_bundle()
        self.write(bundle, "MacOS/demo", signed_macho(b"com.example.demo", None))
        returncode, report = self.run_verify(bundle, "--team-id", "TEAM123")
        self.assertEqual(returncode, 1)
        self.assertEqual([issue["kind"] for issue in report["issues"]], ["team-mismatch"])

    def test_mismatch_beats_team_mismatch(self) -> None:
        bundle = self.make_bundle()
        self.write(bundle, "MacOS/demo", signed_macho(b"com.example.other", b"TEAM123"))
        returncode, report = self.run_verify(bundle, "--team-id", "OTHER99")
        self.assertEqual(returncode, 3)
        self.assertEqual([issue["kind"] for issue in report["issues"]], ["mismatch"])

    def test_bad_superblob_magic_is_metadata(self) -> None:
        bundle = self.make_bundle()
        blob = superblob(code_directory(b"com.example.demo", None), magic=0xDEADBEEF)
        self.write(bundle, "MacOS/demo", thin_macho(blob))
        returncode, report = self.run_verify(bundle)
        self.assertEqual(returncode, 3)
        self.assertEqual([issue["kind"] for issue in report["issues"]], ["metadata"])

    def test_missing_code_directory_is_metadata(self) -> None:
        bundle = self.make_bundle()
        blob = superblob(struct.pack(">II", 0xFADE7171, 8))
        self.write(bundle, "MacOS/demo", thin_macho(blob))
        returncode, report = self.run_verify(bundle)
        self.assertEqual(returncode, 3)
        self.assertEqual([issue["kind"] for issue in report["issues"]], ["metadata"])

    def test_invalid_identity_encoding_is_metadata(self) -> None:
        bundle = self.make_bundle()
        blob = superblob(code_directory(b"\xff\xfe", None))
        self.write(bundle, "MacOS/demo", thin_macho(blob))
        returncode, report = self.run_verify(bundle)
        self.assertEqual(returncode, 3)
        self.assertEqual([issue["kind"] for issue in report["issues"]], ["metadata"])

    def test_fat_binary_is_checked(self) -> None:
        bundle = self.make_bundle()
        signed = signed_macho(b"com.example.demo", b"TEAM123")
        self.write(bundle, "MacOS/demo", fat_macho(thin_macho(), signed))
        returncode, report = self.run_verify(bundle, "--team-id", "TEAM123")
        self.assertEqual(returncode, 0)
        self.assertEqual(report["issues"], [])

    def test_big_endian_slice_is_checked(self) -> None:
        bundle = self.make_bundle()
        blob = superblob(code_directory(b"com.example.demo", None))
        self.write(bundle, "MacOS/demo", thin_macho(blob, endian=">"))
        returncode, report = self.run_verify(bundle)
        self.assertEqual(returncode, 0)
        self.assertEqual(report["issues"], [])

    def test_no_macho_is_exit_4(self) -> None:
        bundle = self.make_bundle()
        self.write(bundle, "Resources/data.bin", b"plain data")
        returncode, report = self.run_verify(bundle)
        self.assertEqual(returncode, 4)
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["totalChecked"], 1)

    def test_empty_contents_is_exit_4(self) -> None:
        bundle = self.make_bundle()
        returncode, report = self.run_verify(bundle)
        self.assertEqual(returncode, 4)
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["totalChecked"], 0)

    def test_issues_sorted_and_unsigned_preferred_exit(self) -> None:
        bundle = self.make_bundle()
        self.write(bundle, "MacOS/btool", signed_macho(b"com.example.btool", b"TEAM123"))
        self.write(bundle, "MacOS/atool", thin_macho())
        returncode, report = self.run_verify(bundle, "--team-id", "OTHER99")
        self.assertEqual(returncode, 1)
        kinds = [(issue["path"], issue["kind"]) for issue in report["issues"]]
        self.assertEqual(
            kinds,
            [("MacOS/atool", "unsigned"), ("MacOS/btool", "team-mismatch")],
        )

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

    @unittest.skipIf(os.geteuid() == 0, "permission checks do not apply to root")
    def test_unreadable_contents_is_exit_3(self) -> None:
        bundle = self.make_bundle()
        os.chmod(bundle / "Contents", 0)
        self.addCleanup(os.chmod, bundle / "Contents", 0o755)
        result = self.invoke("verify-signature", str(bundle))
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout, "")
        self.assertIn(os.path.join(os.path.realpath(bundle), "Contents"), result.stderr)

    def test_bundle_is_not_modified(self) -> None:
        bundle = self.make_bundle()
        payload = signed_macho(b"com.example.demo", b"TEAM123")
        self.write(bundle, "MacOS/demo", payload)
        before = {p.relative_to(bundle): p.stat().st_mtime_ns for p in bundle.rglob("*")}
        returncode, _ = self.run_verify(bundle, "--team-id", "TEAM123")
        self.assertEqual(returncode, 0)
        after = {p.relative_to(bundle): p.stat().st_mtime_ns for p in bundle.rglob("*")}
        self.assertEqual(before, after)
        self.assertEqual((bundle / "Contents" / "MacOS" / "demo").read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
