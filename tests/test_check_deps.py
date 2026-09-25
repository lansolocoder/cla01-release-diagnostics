"""Checks for the check-deps subcommand."""

import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

LC_LOAD_DYLIB = 0x0C
LC_REEXPORT_DYLIB = 0x1F
LC_LOAD_WEAK_DYLIB = 0x80000018
LC_UUID = 0x1B

ARM64 = 0x01000007
X86_64 = 0x0100000C


def dylib_command(name: str, cmd: int = LC_LOAD_DYLIB, endian: str = "<") -> bytes:
    blob = name.encode("utf-8") + b"\x00"
    return struct.pack(endian + "IIIIII", cmd, 24 + len(blob), 24, 0, 0, 0) + blob


def uuid_command(endian: str = "<") -> bytes:
    return struct.pack(endian + "II", LC_UUID, 24) + bytes(range(16))


def thin_macho(cputype: int = ARM64, commands: tuple[bytes, ...] = (), endian: str = "<") -> bytes:
    body = b"".join(commands)
    header = struct.pack(
        endian + "IIIIIIII",
        0xFEEDFACF,
        cputype,
        3,
        2,
        len(commands),
        len(body),
        0,
        0,
    )
    return header + body


def fat_macho(*slices: tuple[int, bytes], endian: str = ">") -> bytes:
    count = len(slices)
    header_size = 8 + 20 * count
    parts = [struct.pack(endian + "II", 0xCAFEBABE, count)]
    offset = header_size
    for cputype, slice_data in slices:
        parts.append(struct.pack(endian + "IIIII", cputype, 3, offset, len(slice_data), 0))
        offset += len(slice_data)
    parts.extend(slice_data for _, slice_data in slices)
    return b"".join(parts)


class CheckDepsTests(unittest.TestCase):
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

    def run_check(self, bundle: Path, *extra: str) -> tuple[int, dict]:
        result = self.invoke("check-deps", str(bundle), *extra)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        report = json.loads(result.stdout)
        self.assertEqual(report["app"], os.path.realpath(bundle))
        self.assertEqual(report["root"], "Contents")
        return result.returncode, report

    def test_thin_binary_reports_arch_and_stripped_libs(self) -> None:
        bundle = self.make_bundle()
        payload = thin_macho(
            X86_64,
            (
                dylib_command("/usr/lib/libSystem.B.dylib"),
                dylib_command("/System/Library/Frameworks/AppKit.framework/Versions/C/AppKit"),
                dylib_command("@rpath/libfoo.dylib"),
                dylib_command("/opt/local/lib/libz.dylib"),
                dylib_command("/usr/lib/libSystem.B.dylib"),
            ),
        )
        self.write(bundle, "MacOS/demo", payload)
        returncode, report = self.run_check(bundle)
        self.assertEqual(returncode, 0)
        self.assertEqual(report["totalChecked"], 1)
        self.assertEqual(report["totalMissing"], 0)
        self.assertEqual(len(report["bins"]), 1)
        entry = report["bins"][0]
        self.assertEqual(entry["path"], "MacOS/demo")
        self.assertEqual(entry["arch"], "x86_64")
        self.assertNotIn("missing", entry)
        self.assertEqual(
            entry["libs"],
            [
                "/opt/local/lib/libz.dylib",
                "@rpath/libfoo.dylib",
                "Frameworks/AppKit.framework/Versions/C/AppKit",
                "libSystem.B.dylib",
            ],
        )

    def test_prefix_stripping_is_literal_and_case_sensitive(self) -> None:
        bundle = self.make_bundle()
        payload = thin_macho(ARM64, (dylib_command("/USR/LIB/libx.dylib"),))
        self.write(bundle, "MacOS/demo", payload)
        returncode, report = self.run_check(bundle)
        self.assertEqual(returncode, 0)
        self.assertEqual(report["bins"][0]["libs"], ["/USR/LIB/libx.dylib"])

    def test_weak_and_reexport_commands_are_listed(self) -> None:
        bundle = self.make_bundle()
        payload = thin_macho(
            ARM64,
            (
                dylib_command("/usr/lib/libweak.dylib", cmd=LC_LOAD_WEAK_DYLIB),
                dylib_command("/usr/lib/libreexport.dylib", cmd=LC_REEXPORT_DYLIB),
                uuid_command(),
            ),
        )
        self.write(bundle, "MacOS/demo", payload)
        returncode, report = self.run_check(bundle)
        self.assertEqual(returncode, 0)
        self.assertEqual(report["bins"][0]["libs"], ["libreexport.dylib", "libweak.dylib"])

    def test_fat_binary_merges_slice_libs_with_null_arch(self) -> None:
        bundle = self.make_bundle()
        arm = thin_macho(ARM64, (dylib_command("/usr/lib/libarm.dylib"),))
        intel = thin_macho(X86_64, (dylib_command("/usr/lib/libintel.dylib"),))
        self.write(bundle, "MacOS/demo", fat_macho((ARM64, arm), (X86_64, intel)))
        returncode, report = self.run_check(bundle)
        self.assertEqual(returncode, 0)
        self.assertEqual(report["totalChecked"], 1)
        self.assertEqual(len(report["bins"]), 1)
        entry = report["bins"][0]
        self.assertIsNone(entry["arch"])
        self.assertEqual(entry["libs"], ["libarm.dylib", "libintel.dylib"])

    def test_require_arch_emits_one_record_per_slice(self) -> None:
        bundle = self.make_bundle()
        arm = thin_macho(ARM64, (dylib_command("/usr/lib/libarm.dylib"),))
        intel = thin_macho(X86_64, (dylib_command("/usr/lib/libintel.dylib"),))
        self.write(bundle, "MacOS/demo", fat_macho((X86_64, intel), (ARM64, arm)))
        returncode, report = self.run_check(bundle, "--require-arch", "arm64")
        self.assertEqual(returncode, 0)
        self.assertEqual(report["totalMissing"], 0)
        records = [(entry["arch"], entry["libs"]) for entry in report["bins"]]
        self.assertEqual(
            records,
            [("arm64", ["libarm.dylib"]), ("x86_64", ["libintel.dylib"])],
        )

    def test_require_arch_missing_appends_record_and_exits_1(self) -> None:
        bundle = self.make_bundle()
        self.write(bundle, "MacOS/demo", thin_macho(X86_64, (dylib_command("/usr/lib/liba.dylib"),)))
        returncode, report = self.run_check(bundle, "--require-arch", "arm64")
        self.assertEqual(returncode, 1)
        self.assertEqual(report["totalChecked"], 1)
        self.assertEqual(report["totalMissing"], 1)
        self.assertEqual(
            report["bins"],
            [
                {"arch": None, "libs": [], "missing": "arm64", "path": "MacOS/demo"},
                {"arch": "x86_64", "libs": ["liba.dylib"], "path": "MacOS/demo"},
            ],
        )

    def test_unknown_cputype_uses_hex_name(self) -> None:
        bundle = self.make_bundle()
        self.write(bundle, "MacOS/demo", thin_macho(0x01000002))
        returncode, report = self.run_check(bundle, "--require-arch", "arm64")
        self.assertEqual(returncode, 1)
        arches = [entry["arch"] for entry in report["bins"]]
        self.assertEqual(arches, [None, "0x01000002"])

    def test_bins_sorted_by_path_then_arch(self) -> None:
        bundle = self.make_bundle()
        self.write(bundle, "MacOS/btool", thin_macho(X86_64))
        self.write(bundle, "MacOS/atool", thin_macho(ARM64))
        returncode, report = self.run_check(bundle, "--require-arch", "x86_64")
        self.assertEqual(returncode, 1)
        keys = [(entry["path"], entry["arch"]) for entry in report["bins"]]
        self.assertEqual(
            keys,
            [
                ("MacOS/atool", None),
                ("MacOS/atool", "arm64"),
                ("MacOS/btool", "x86_64"),
            ],
        )

    def test_symlinks_and_non_macho_are_ignored(self) -> None:
        bundle = self.make_bundle()
        payload = thin_macho(ARM64)
        self.write(bundle, "MacOS/demo", payload)
        self.write(bundle, "Resources/data.bin", b"plain data")
        os.symlink("MacOS/demo", bundle / "Contents" / "runner")
        returncode, report = self.run_check(bundle)
        self.assertEqual(returncode, 0)
        self.assertEqual(report["totalChecked"], 1)
        self.assertEqual([entry["path"] for entry in report["bins"]], ["MacOS/demo"])

    def test_malformed_macho_is_ignored(self) -> None:
        bundle = self.make_bundle()
        self.write(bundle, "MacOS/truncated", struct.pack("<IIII", 0xFEEDFACF, ARM64, 3, 2))
        self.write(bundle, "MacOS/demo", thin_macho(ARM64))
        returncode, report = self.run_check(bundle)
        self.assertEqual(returncode, 0)
        self.assertEqual(report["totalChecked"], 1)

    def test_no_macho_is_exit_4(self) -> None:
        bundle = self.make_bundle()
        self.write(bundle, "Resources/data.bin", b"plain data")
        returncode, report = self.run_check(bundle, "--require-arch", "arm64")
        self.assertEqual(returncode, 4)
        self.assertEqual(report["bins"], [])
        self.assertEqual(report["totalChecked"], 0)
        self.assertEqual(report["totalMissing"], 0)

    def test_invalid_require_arch_is_exit_2(self) -> None:
        bundle = self.make_bundle()
        result = self.invoke("check-deps", str(bundle), "--require-arch", "ppc")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("ppc", result.stderr)
        self.assertEqual(len(result.stderr.strip().splitlines()), 1)

    def test_missing_path_is_exit_2(self) -> None:
        missing = str(self.base / "Nope.app")
        result = self.invoke("check-deps", missing)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(missing, result.stderr)

    def test_non_app_directory_is_exit_2(self) -> None:
        target = self.base / "Demo.bundle"
        target.mkdir()
        result = self.invoke("check-deps", str(target))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(target), result.stderr)

    def test_missing_contents_is_exit_3(self) -> None:
        bundle = self.base / "Empty.app"
        bundle.mkdir()
        result = self.invoke("check-deps", str(bundle))
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout, "")
        self.assertIn(os.path.join(os.path.realpath(bundle), "Contents"), result.stderr)

    @unittest.skipIf(os.geteuid() == 0, "permission checks do not apply to root")
    def test_unreadable_contents_is_exit_3(self) -> None:
        bundle = self.make_bundle()
        os.chmod(bundle / "Contents", 0)
        self.addCleanup(os.chmod, bundle / "Contents", 0o755)
        result = self.invoke("check-deps", str(bundle))
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout, "")
        self.assertIn(os.path.join(os.path.realpath(bundle), "Contents"), result.stderr)

    def test_bundle_is_not_modified(self) -> None:
        bundle = self.make_bundle()
        payload = thin_macho(ARM64, (dylib_command("/usr/lib/liba.dylib"),))
        self.write(bundle, "MacOS/demo", payload)
        before = {p.relative_to(bundle): p.stat().st_mtime_ns for p in bundle.rglob("*")}
        returncode, _ = self.run_check(bundle, "--require-arch", "arm64")
        self.assertEqual(returncode, 0)
        after = {p.relative_to(bundle): p.stat().st_mtime_ns for p in bundle.rglob("*")}
        self.assertEqual(before, after)
        self.assertEqual((bundle / "Contents" / "MacOS" / "demo").read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
