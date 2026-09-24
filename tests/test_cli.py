"""Checks for the documented command-line entry point."""

from pathlib import Path
import json
import plistlib
import struct
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def make_app(info: dict | None, executable_name: str | None = None,
             executable_bytes: bytes | None = None) -> Path:
    """Create a minimal .app bundle in a temporary directory."""
    tmp = Path(tempfile.mkdtemp())
    app = tmp / "Sample.app"
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    if info is not None:
        (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    if executable_name is not None:
        binary = contents / "MacOS" / executable_name
        binary.write_bytes(executable_bytes if executable_bytes is not None else b"")
    return app


def thin_macho(cputype: int, magic: int = 0xFEEDFACF,
               dylibs: list[str] | None = None) -> bytes:
    """Build a minimal little-endian Mach-O with LC_LOAD_DYLIB commands."""
    commands = b""
    for name in dylibs or []:
        raw = name.encode() + b"\x00"
        cmdsize = 24 + len(raw)
        commands += struct.pack("<II", 0x0C, cmdsize)
        commands += struct.pack("<IIII", 24, 0, 0, 0) + raw
    header = struct.pack("<IiiIIII", magic, cputype, 3, 2,
                         len(dylibs or []), len(commands), 0)
    if magic == 0xFEEDFACF:
        header += struct.pack("<I", 0)
    return header + commands


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

    def test_scan_valid_app(self) -> None:
        app = make_app(
            {
                "CFBundleIdentifier": "com.example.sample",
                "CFBundleVersion": "9.9",
                "CFBundleExecutable": "Sample",
            },
            "Sample",
            thin_macho(0x01000007, dylibs=["/usr/lib/libB.dylib", "/usr/lib/libA.dylib"]),
        )
        result = self.invoke("scan", str(app))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        report = json.loads(result.stdout)
        self.assertEqual(report["app_path"], str(app))
        self.assertEqual(report["bundle_identifier"], "com.example.sample")
        self.assertEqual(report["bundle_version"], "9.9")
        self.assertEqual(
            report["executable"], str(app / "Contents" / "MacOS" / "Sample")
        )
        self.assertEqual(report["architectures"], ["x86_64"])
        self.assertEqual(
            report["linked_libraries"], ["/usr/lib/libA.dylib", "/usr/lib/libB.dylib"]
        )
        self.assertEqual(report["issues"], [])

    def test_scan_fat_binary_and_unknown_cputype(self) -> None:
        slice1 = thin_macho(0x12345678, dylibs=["/usr/lib/libZ.dylib"])
        slice2 = thin_macho(0x0100000C, dylibs=["/usr/lib/libZ.dylib"])
        aligned = (len(slice1) + 7) & ~7
        blob = struct.pack(">II", 0xCAFEBABE, 2)
        blob += struct.pack(">iiIII", 0x12345678, 0, 48, len(slice1), 3)
        blob += struct.pack(">iiIII", 0x0100000C, 0, 48 + aligned, len(slice2), 3)
        blob += b"\x00" * (48 - len(blob)) + slice1
        blob += b"\x00" * (48 + aligned - len(blob)) + slice2
        app = make_app({"CFBundleExecutable": "Fat"}, "Fat", blob)

        report = json.loads(self.invoke("scan", str(app)).stdout)
        self.assertEqual(report["architectures"], ["0x12345678", "arm64"])
        self.assertEqual(report["linked_libraries"], ["/usr/lib/libZ.dylib"])

    def test_scan_non_macho_executable(self) -> None:
        app = make_app({"CFBundleExecutable": "Tool"}, "Tool", b"#!/bin/sh\n")
        report = json.loads(self.invoke("scan", str(app)).stdout)
        self.assertEqual(report["architectures"], [])
        self.assertEqual(report["linked_libraries"], [])
        self.assertEqual(report["issues"], [])

    def test_scan_missing_executable(self) -> None:
        app = make_app({"CFBundleExecutable": "Gone"})
        result = self.invoke("scan", str(app))
        self.assertEqual(result.returncode, 0)
        report = json.loads(result.stdout)
        self.assertIsNone(report["executable"])
        self.assertEqual(len(report["issues"]), 1)
        self.assertEqual(report["issues"][0]["code"], "missing_executable")

    def test_scan_invalid_app_packages(self) -> None:
        cases = {
            "file instead of directory": make_app(
                {"CFBundleExecutable": "x"}
            ).parent / "NotAnApp",
            "missing plist": make_app(None),
        }
        cases["file instead of directory"].write_bytes(b"x")
        malformed = make_app({"CFBundleExecutable": "x"})
        (malformed / "Contents" / "Info.plist").write_text("<plist><dict>")
        cases["malformed plist"] = malformed

        for label, app in cases.items():
            with self.subTest(label):
                result = self.invoke("scan", str(app))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertIn("invalid_app_package", result.stderr)

    def test_scan_rejects_extra_arguments(self) -> None:
        for arguments in [("scan",), ("scan", "a.app", "b.app"),
                          ("scan", "a.app", "--verbose")]:
            with self.subTest(arguments=arguments):
                result = self.invoke(*arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
