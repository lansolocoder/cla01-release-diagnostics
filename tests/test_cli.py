"""Checks for the documented command-line entry point."""

from pathlib import Path
import json
import struct
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

INFO_PLIST = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>com.example.App</string>
  <key>CFBundleVersion</key><string>1.2.3</string>
  <key>CFBundleExecutable</key><string>App</string>
</dict></plist>
"""


def build_thin_macho(cputype: int, libs: list[str] | None = None) -> bytes:
    """Build a minimal big-endian thin Mach-O with LC_LOAD_DYLIB commands."""
    commands = b""
    for lib in libs or []:
        name = lib.encode("utf-8") + b"\x00"
        name_offset = 24  # cmd, cmdsize, lc_str, timestamp, versions
        cmdsize = name_offset + len(name)
        commands += struct.pack(">IIIIII", 0x0C, cmdsize, name_offset, 0, 0, 0)
        commands += name
    header = struct.pack(
        ">IiiIIIII",
        0xFEEDFACF,
        cputype,
        0,
        2,  # MH_EXECUTE
        len(libs or []),
        len(commands),
        0,
        0,  # reserved (mach_header_64)
    )
    return header + commands


def build_fat_macho(slices: list[tuple[int, bytes]]) -> bytes:
    """Build a big-endian fat binary wrapping the given thin slices."""
    header = struct.pack(">II", 0xCAFEBABE, len(slices))
    entries = b""
    bodies = b""
    offset = 8 + 20 * len(slices)
    for cputype, body in slices:
        entries += struct.pack(
            ">iiIII",
            cputype,
            0,
            offset,
            len(body),
            0x4000,
        )
        bodies += body
        offset += len(body)
    return header + entries + bodies


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


class ScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.app = self.tmp / "Example.app"
        (self.app / "Contents" / "MacOS").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "release_workbench", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def write_app(self, executable_bytes: bytes | None) -> dict:
        (self.app / "Contents" / "Info.plist").write_text(INFO_PLIST)
        if executable_bytes is not None:
            (self.app / "Contents" / "MacOS" / "App").write_bytes(
                executable_bytes
            )
        result = self.invoke("scan", str(self.app))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def test_scan_thin_arm64(self) -> None:
        macho = build_thin_macho(
            0x0100000C,
            ["/usr/lib/libSystem.B.dylib", "/usr/lib/libz.1.dylib"],
        )
        report = self.write_app(macho)
        self.assertEqual(report["app_path"], str(self.app))
        self.assertEqual(report["bundle_identifier"], "com.example.App")
        self.assertEqual(report["bundle_version"], "1.2.3")
        self.assertEqual(
            report["executable"], str(self.app / "Contents" / "MacOS" / "App")
        )
        self.assertEqual(report["architectures"], ["arm64"])
        self.assertEqual(
            report["linked_libraries"],
            ["/usr/lib/libSystem.B.dylib", "/usr/lib/libz.1.dylib"],
        )
        self.assertEqual(report["issues"], [])

    def test_scan_fat_binary_sorted_dedup(self) -> None:
        arm = build_thin_macho(0x0100000C, ["/usr/lib/libSystem.B.dylib"])
        intel = build_thin_macho(
            0x01000007,
            ["/usr/lib/libSystem.B.dylib", "/usr/lib/libc++.1.dylib"],
        )
        report = self.write_app(build_fat_macho([(0x0100000C, arm), (0x01000007, intel)]))
        self.assertEqual(report["architectures"], ["arm64", "x86_64"])
        self.assertEqual(
            report["linked_libraries"],
            ["/usr/lib/libSystem.B.dylib", "/usr/lib/libc++.1.dylib"],
        )
        self.assertEqual(report["issues"], [])

    def test_scan_unknown_cputype_is_hex(self) -> None:
        report = self.write_app(build_thin_macho(99))
        self.assertEqual(report["architectures"], ["0x63"])

    def test_scan_non_macho(self) -> None:
        report = self.write_app(b"#!/bin/sh\necho hello\n")
        self.assertEqual(report["architectures"], [])
        self.assertEqual(report["linked_libraries"], [])
        self.assertEqual(report["issues"], [])

    def test_scan_missing_executable_file(self) -> None:
        (self.app / "Contents" / "Info.plist").write_text(INFO_PLIST)
        result = self.invoke("scan", str(self.app))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIsNone(report["executable"])
        self.assertEqual(len(report["issues"]), 1)
        self.assertEqual(report["issues"][0]["code"], "missing_executable")

    def test_scan_missing_plist_fields_are_null(self) -> None:
        (self.app / "Contents" / "Info.plist").write_text(
            '<?xml version="1.0"?><plist version="1.0"><dict>'
            "<key>CFBundleIdentifier</key><integer>1</integer>"
            "</dict></plist>"
        )
        result = self.invoke("scan", str(self.app))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIsNone(report["bundle_identifier"])
        self.assertIsNone(report["bundle_version"])
        self.assertIsNone(report["executable"])
        self.assertEqual(report["issues"][0]["code"], "missing_executable")

    def test_scan_invalid_package_cases(self) -> None:
        not_dir = self.tmp / "NotAnApp.app"
        not_dir.write_text("x")
        missing_plist = self.tmp / "Empty.app"
        (missing_plist / "Contents").mkdir(parents=True)
        bad_plist = self.tmp / "Bad.app"
        (bad_plist / "Contents").mkdir(parents=True)
        (bad_plist / "Contents" / "Info.plist").write_text("not a plist")

        for path in (not_dir, missing_plist, bad_plist):
            with self.subTest(path=path.name):
                result = self.invoke("scan", str(path))
                self.assertEqual(result.returncode, 2)
                self.assertIn("invalid_app_package", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_scan_rejects_extra_arguments(self) -> None:
        result = self.invoke("scan", str(self.app), "extra")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
