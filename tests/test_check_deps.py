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

LC_LOAD_DYLIB = 0xC
LC_ID_DYLIB = 0xD
LC_LOAD_WEAK_DYLIB = 0x80000018
LC_REEXPORT_DYLIB = 0x800001F
LC_SEGMENT_64 = 0x19

ARM64 = 0x01000007
X86_64 = 0x0100000C
OTHER_CPU = 0x01000010


def load_command(cmd: int, path: str, endian: str = "<") -> bytes:
    name = path.encode("utf-8") + b"\x00"
    cmdsize = 24 + len(name)
    padding = (-cmdsize) % 8
    cmdsize += padding
    name += b"\x00" * padding
    return struct.pack(endian + "IIIIII", cmd, cmdsize, 24, 0, 0, 0) + name


def segment_command(endian: str = "<") -> bytes:
    # LC_SEGMENT_64 with an empty segment; must never appear in libs.
    return struct.pack(endian + "II16sQQQQiiII", LC_SEGMENT_64, 72, b"", 0, 0, 0, 0, 0, 0, 0, 0)


def thin_macho(cputype: int, commands: list[bytes] | None = None, endian: str = "<") -> bytes:
    commands = commands or []
    body = b"".join(commands)
    header = struct.pack(
        endian + "IIIIIIII",
        0xFEEDFACF, cputype, 3, 2, len(commands), len(body), 0, 0,
    )
    return header + body


def fat_macho(slices: list[tuple[int, bytes]], endian: str = ">") -> bytes:
    count = len(slices)
    header_size = 8 + 20 * count
    parts = [struct.pack(endian + "II", 0xCAFEBABE, count)]
    offset = header_size
    bodies = []
    for cputype, slice_data in slices:
        parts.append(struct.pack(endian + "IIIII", cputype, 3, offset, len(slice_data), 0))
        bodies.append(slice_data)
        offset += len(slice_data)
    parts.extend(bodies)
    return b"".join(parts)


def dylibs(*paths: str, cmd: int = LC_LOAD_DYLIB, endian: str = "<") -> list[bytes]:
    return [load_command(cmd, path, endian) for path in paths]


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
        (bundle / "Contents" / "MacOS").mkdir(parents=True)
        (bundle / "Contents" / "Frameworks").mkdir(parents=True)
        return bundle

    def write(self, bundle: Path, relative: str, data: bytes) -> None:
        target = bundle / "Contents" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def run_check(self, bundle: Path, *extra: str) -> tuple[int, dict]:
        result = self.invoke("check-deps", str(bundle), *extra)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(len(result.stdout.strip().splitlines()), 1)
        report = json.loads(result.stdout)
        self.assertEqual(report["app"], os.path.realpath(bundle))
        self.assertEqual(report["root"], "Contents")
        return result.returncode, report

    def test_thin_macho_libs_filtered_sorted_and_deduped(self) -> None:
        bundle = self.make_bundle()
        commands = dylibs(
            "/usr/lib/libSystem.B.dylib",
            "@rpath/Dep.framework/Dep",
            "/System/Library/Frameworks/Foo.framework/Foo",
            "/opt/local/lib/libz.dylib",
        ) + dylibs("@rpath/Dep.framework/Dep", cmd=LC_LOAD_WEAK_DYLIB)
        self.write(bundle, "MacOS/demo", thin_macho(ARM64, commands))
        returncode, report = self.run_check(bundle)
        self.assertEqual(returncode, 0)
        self.assertEqual(report["totalChecked"], 1)
        self.assertEqual(report["totalMissing"], 0)
        (entry,) = report["bins"]
        self.assertEqual(
            entry,
            {
                "path": "MacOS/demo",
                "arch": None,
                "libs": ["/opt/local/lib/libz.dylib", "@rpath/Dep.framework/Dep"],
            },
        )

    def test_load_command_kinds_and_non_dylib_commands(self) -> None:
        bundle = self.make_bundle()
        commands = [
            load_command(LC_LOAD_DYLIB, "@rpath/A.framework/A"),
            load_command(LC_LOAD_WEAK_DYLIB, "@rpath/B.framework/B"),
            load_command(LC_REEXPORT_DYLIB, "@rpath/C.framework/C"),
            load_command(LC_ID_DYLIB, "@rpath/Ignored.framework/Ignored"),
            segment_command(),
        ]
        self.write(bundle, "MacOS/demo", thin_macho(ARM64, commands))
        _returncode, report = self.run_check(bundle)
        (entry,) = report["bins"]
        self.assertEqual(
            entry["libs"],
            ["@rpath/A.framework/A", "@rpath/B.framework/B", "@rpath/C.framework/C"],
        )

    def test_prefix_matching_is_literal_and_case_sensitive(self) -> None:
        bundle = self.make_bundle()
        commands = dylibs(
            "/usr/lib2/libx.dylib",
            "/usr/Lib/libx.dylib",
            "/system/library/libx.dylib",
            "/System/Library/",
            "/usr/lib/",
        )
        self.write(bundle, "MacOS/demo", thin_macho(ARM64, commands))
        _returncode, report = self.run_check(bundle)
        (entry,) = report["bins"]
        self.assertEqual(
            entry["libs"],
            ["/system/library/libx.dylib", "/usr/Lib/libx.dylib", "/usr/lib2/libx.dylib"],
        )

    def test_fat_without_require_merges_slices(self) -> None:
        bundle = self.make_bundle()
        arm_slice = thin_macho(ARM64, dylibs("@rpath/Shared.framework/Shared", "@rpath/Arm.framework/Arm"))
        x86_slice = thin_macho(X86_64, dylibs("@rpath/Shared.framework/Shared", "@rpath/X86.framework/X86"))
        self.write(bundle, "MacOS/demo", fat_macho([(ARM64, arm_slice), (X86_64, x86_slice)]))
        returncode, report = self.run_check(bundle)
        self.assertEqual(returncode, 0)
        self.assertEqual(report["totalChecked"], 1)
        (entry,) = report["bins"]
        self.assertEqual(entry["arch"], None)
        self.assertEqual(
            entry["libs"],
            [
                "@rpath/Arm.framework/Arm",
                "@rpath/Shared.framework/Shared",
                "@rpath/X86.framework/X86",
            ],
        )

    def test_require_arch_emits_one_record_per_slice(self) -> None:
        bundle = self.make_bundle()
        arm_slice = thin_macho(ARM64, dylibs("@rpath/Arm.framework/Arm"))
        x86_slice = thin_macho(X86_64, dylibs("@rpath/X86.framework/X86"))
        self.write(bundle, "MacOS/demo", fat_macho([(X86_64, x86_slice), (ARM64, arm_slice)]))
        returncode, report = self.run_check(bundle, "--require-arch", "arm64")
        self.assertEqual(returncode, 0)
        self.assertEqual(report["totalChecked"], 1)
        self.assertEqual(report["totalMissing"], 0)
        self.assertEqual(
            report["bins"],
            [
                {"path": "MacOS/demo", "arch": "arm64", "libs": ["@rpath/Arm.framework/Arm"]},
                {"path": "MacOS/demo", "arch": "x86_64", "libs": ["@rpath/X86.framework/X86"]},
            ],
        )
        for entry in report["bins"]:
            self.assertNotIn("missing", entry)

    def test_missing_arch_appends_null_record_and_exit_1(self) -> None:
        bundle = self.make_bundle()
        self.write(bundle, "MacOS/demo", thin_macho(ARM64, dylibs("@rpath/Arm.framework/Arm")))
        returncode, report = self.run_check(bundle, "--require-arch", "x86_64")
        self.assertEqual(returncode, 1)
        self.assertEqual(report["totalChecked"], 1)
        self.assertEqual(report["totalMissing"], 1)
        self.assertEqual(
            report["bins"],
            [
                {"path": "MacOS/demo", "arch": None, "missing": "x86_64", "libs": []},
                {"path": "MacOS/demo", "arch": "arm64", "libs": ["@rpath/Arm.framework/Arm"]},
            ],
        )

    def test_fat_missing_required_slice(self) -> None:
        bundle = self.make_bundle()
        x86_slice = thin_macho(X86_64, dylibs("@rpath/X86.framework/X86"))
        self.write(bundle, "MacOS/demo", fat_macho([(X86_64, x86_slice)]))
        returncode, report = self.run_check(bundle, "--require-arch", "arm64")
        self.assertEqual(returncode, 1)
        self.assertEqual(report["totalMissing"], 1)
        self.assertEqual(
            report["bins"],
            [
                {"path": "MacOS/demo", "arch": None, "missing": "arm64", "libs": []},
                {"path": "MacOS/demo", "arch": "x86_64", "libs": ["@rpath/X86.framework/X86"]},
            ],
        )

    def test_unknown_cputype_uses_hex_name(self) -> None:
        bundle = self.make_bundle()
        self.write(bundle, "MacOS/demo", thin_macho(OTHER_CPU, dylibs("@rpath/Weird.framework/Weird")))
        returncode, report = self.run_check(bundle, "--require-arch", "arm64")
        self.assertEqual(returncode, 1)
        names = [(entry["arch"], entry.get("missing")) for entry in report["bins"]]
        self.assertEqual(
            names,
            [(None, "arm64"), ("0x01000010", None)],
        )

    def test_big_endian_and_little_endian_fat(self) -> None:
        bundle = self.make_bundle()
        self.write(
            bundle,
            "MacOS/be",
            thin_macho(ARM64, dylibs("@rpath/BE.framework/BE", endian=">"), endian=">"),
        )
        arm_slice = thin_macho(ARM64, dylibs("@rpath/A.framework/A"))
        self.write(bundle, "MacOS/lefat", self._little_endian_fat(arm_slice))
        returncode, report = self.run_check(bundle)
        self.assertEqual(returncode, 0)
        self.assertEqual(
            [(entry["path"], entry["libs"]) for entry in report["bins"]],
            [
                ("MacOS/be", ["@rpath/BE.framework/BE"]),
                ("MacOS/lefat", ["@rpath/A.framework/A"]),
            ],
        )

    @staticmethod
    def _little_endian_fat(slice_data: bytes) -> bytes:
        parts = [struct.pack("<II", 0xCAFEBABE, 1)]
        parts.append(struct.pack("<IIIII", ARM64, 3, 28, len(slice_data), 0))
        parts.append(slice_data)
        return b"".join(parts)

    def test_bins_sorted_by_path_then_arch(self) -> None:
        bundle = self.make_bundle()
        self.write(bundle, "MacOS/zed", thin_macho(ARM64))
        self.write(bundle, "Frameworks/A.framework/A", thin_macho(X86_64))
        self.write(bundle, "MacOS/abe", thin_macho(ARM64))
        _returncode, report = self.run_check(bundle)
        self.assertEqual(
            [entry["path"] for entry in report["bins"]],
            ["Frameworks/A.framework/A", "MacOS/abe", "MacOS/zed"],
        )

    def test_non_macho_and_symlinks_are_ignored(self) -> None:
        bundle = self.make_bundle()
        target = self.base / "outside-macho"
        target.write_bytes(thin_macho(ARM64, dylibs("@rpath/Outside.framework/Outside")))
        os.symlink(target, bundle / "Contents" / "MacOS" / "link")
        os.symlink("missing-target", bundle / "Contents" / "MacOS" / "broken")
        self.write(bundle, "Resources/data.bin", b"plain data")
        self.write(bundle, "Resources/fake-macho", b"\xcf\xfa\xed\xfejunk")
        self.write(bundle, "MacOS/demo", thin_macho(ARM64, dylibs("@rpath/Dep.framework/Dep")))
        returncode, report = self.run_check(bundle)
        self.assertEqual(returncode, 0)
        self.assertEqual(report["totalChecked"], 1)
        self.assertEqual([entry["path"] for entry in report["bins"]], ["MacOS/demo"])

    def test_no_macho_is_exit_4(self) -> None:
        bundle = self.make_bundle()
        self.write(bundle, "Resources/data.bin", b"plain data")
        returncode, report = self.run_check(bundle)
        self.assertEqual(returncode, 4)
        self.assertEqual(report["bins"], [])
        self.assertEqual(report["totalChecked"], 0)
        self.assertEqual(report["totalMissing"], 0)

    def test_empty_contents_is_exit_4(self) -> None:
        bundle = self.make_bundle()
        returncode, report = self.run_check(bundle)
        self.assertEqual(returncode, 4)
        self.assertEqual(report["totalChecked"], 0)

    def test_invalid_require_arch_is_exit_2(self) -> None:
        bundle = self.make_bundle()
        self.write(bundle, "MacOS/demo", thin_macho(ARM64))
        for bad in ("ppc", "ARM64", "x86-64", ""):
            with self.subTest(bad=bad):
                result = self.invoke("check-deps", str(bundle), "--require-arch", bad)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertTrue(result.stderr.endswith("\n"))
                self.assertEqual(len(result.stderr.strip().splitlines()), 1)

    def test_missing_path_is_exit_2(self) -> None:
        missing = str(self.base / "Nope.app")
        result = self.invoke("check-deps", missing, "--require-arch", "arm64")
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

    def test_bundle_is_not_modified(self) -> None:
        bundle = self.make_bundle()
        payload = thin_macho(ARM64, dylibs("@rpath/Dep.framework/Dep"))
        self.write(bundle, "MacOS/demo", payload)
        before = {p.relative_to(bundle): p.stat().st_mtime_ns for p in bundle.rglob("*")}
        returncode, _ = self.run_check(bundle, "--require-arch", "x86_64")
        self.assertEqual(returncode, 1)
        after = {p.relative_to(bundle): p.stat().st_mtime_ns for p in bundle.rglob("*")}
        self.assertEqual(before, after)
        self.assertEqual((bundle / "Contents" / "MacOS" / "demo").read_bytes(), payload)

    def test_app_path_has_no_trailing_slash_and_is_realpath(self) -> None:
        bundle = self.make_bundle()
        self.write(bundle, "MacOS/demo", thin_macho(ARM64))
        returncode, report = self.run_check(bundle)
        self.assertEqual(returncode, 0)
        self.assertFalse(report["app"].endswith("/"))
        self.assertEqual(report["app"], os.path.realpath(bundle))


if __name__ == "__main__":
    unittest.main()
