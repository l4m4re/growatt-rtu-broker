from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from tools.analyze_shine_firmware import aligned_headers, analyze, pattern_hits


def make_image() -> bytes:
    payload = b"stock server.growatt.com:5279\x00"
    header = bytes((0xE9, 1, 0, 0x40)) + struct.pack("<I", 0x40100000)
    segment = struct.pack("<II", 0x40100000, len(payload)) + payload
    checksum = 0xEF
    for value in payload:
        checksum ^= value
    image = header + segment
    image += b"\x00" * ((15 - len(image) % 16) % 16)
    return image + bytes((checksum,)) + b"\xff" * 32


class FirmwareAnalysisTest(unittest.TestCase):
    def test_parse_aligned_esp_header(self) -> None:
        image = make_image()
        headers = aligned_headers(image)

        self.assertEqual(len(headers), 1)
        self.assertEqual(headers[0]["segment_count"], 1)
        self.assertEqual(headers[0]["flash_mode"], "QIO")
        self.assertEqual(headers[0]["entry_point"], "0x40100000")
        self.assertTrue(headers[0]["checksum_valid"])

    def test_known_patterns_keep_offsets_without_interpretation(self) -> None:
        data = b"\x00\x03\x00\x2b\x00\x01" + b"\x01\x20\x00\x00\x00\x64"
        hits = pattern_hits(data)

        self.assertEqual(hits["discovery_prefix"], [0])
        self.assertEqual(hits["fc20_prefix"], [6])
        self.assertEqual(hits["count_100_be"], [10])

    def test_analyze_reports_hash_and_keyword_strings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.bin"
            path.write_bytes(make_image())

            result = analyze(path)

            self.assertEqual(result["size"], path.stat().st_size)
            self.assertEqual(len(result["sha256"]), 64)
            self.assertGreater(result["keyword_strings"]["server"][0]["offset"], 0)
