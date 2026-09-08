from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.diff_shine_flash import analyze as analyze_diff
from tools.find_shine_identity import analyze as analyze_identity


class ShineForensicsTest(unittest.TestCase):
    def test_identity_search_reports_encoded_and_partial_hits(self) -> None:
        data = b"\xff" * 32 + "XGD6CCN109".encode("utf-16le") + b"\xffXGD"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shine.bin"
            path.write_bytes(data)
            result = analyze_identity(path, "XGD6CCN109")

        encodings = {hit["encoding"] for hit in result["exact_or_encoded_hits"]}
        self.assertIn("utf16le", encodings)
        self.assertEqual(result["partial_token_hits"]["XGD"], [len(data) - 3])

    def test_diff_reports_sector_and_interval_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base_path = Path(directory) / "base.bin"
            target_path = Path(directory) / "target.bin"
            base_path.write_bytes(b"\0" * 0x2000)
            target = bytearray(b"\0" * 0x2000)
            target[0x1000:0x1004] = b"test"
            target_path.write_bytes(target)
            result = analyze_diff(base_path, target_path)

        self.assertEqual(result["changed_bytes"], 4)
        self.assertEqual(result["changed_sectors"], 1)
        self.assertEqual(result["coalesced_different_intervals"][0]["start"], 0x1000)

    def test_identity_search_reports_mac_and_chip_id_forms(self) -> None:
        data = b"\xff" * 8 + bytes.fromhex("e868e774f8de") + b"ESP_74F8DE"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shine.bin"
            path.write_bytes(data)
            result = analyze_identity(path, "XGD6CCN109", mac="e8:68:e7:74:f8:de", chip_id="0x0074f8de")

        matches = {(hit["search_kind"], hit["encoding"], hit["offset"]) for hit in result["exact_or_encoded_hits"]}
        self.assertIn(("mac", "mac_raw", 8), matches)
        self.assertIn(("chip_id", "chip_id_24_be", 11), matches)
