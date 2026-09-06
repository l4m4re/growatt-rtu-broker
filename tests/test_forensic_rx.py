"""Tests for conservative asynchronous physical RX classification."""

from __future__ import annotations

import json

from growatt_broker.broker import add_crc
from tools.analyze_forensic_rx import analyze


def _tx(function: int, start: int, count: int) -> dict:
    raw = add_crc(
        bytes([1, function])
        + start.to_bytes(2, "big")
        + count.to_bytes(2, "big")
    )
    return {"event": "physical_tx", "ts": "2026-09-06T00:00:00Z", "hex": raw.hex()}


def _rx(raw: bytes, source: str = "normal_read") -> dict:
    return {
        "event": "physical_rx_raw",
        "kind": "serial_read",
        "captured_bytes": True,
        "source": source,
        "ts": "2026-09-06T00:00:01Z",
        "hex": raw.hex(),
    }


def test_expected_standard_and_fc20_are_separated(tmp_path) -> None:
    standard = add_crc(bytes.fromhex("01040400010002"))
    fc20 = add_crc(bytes.fromhex("0120020001"))
    path = tmp_path / "capture.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(record)
            for record in [_tx(0x04, 3000, 2), _rx(b"\xAA" + standard + fc20)]
        )
        + "\n"
    )

    result = analyze(path)

    assert result["raw_rx_bytes"] == 1 + len(standard) + len(fc20)
    assert result["fc_0x20_observed"] is True
    assert result["categories"]["EXPECTED_STANDARD_RESPONSE"] == 1
    assert result["categories"]["UNSOLICITED_VALID_CRC_FRAME"] == 1
    assert result["unknown_bytes"] == 1
    assert result["function_codes"]["32"]["hex"] == "0x20"


def test_duplicate_matching_response_is_late_not_expected(tmp_path) -> None:
    standard = add_crc(bytes.fromhex("0104020001"))
    path = tmp_path / "capture.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(record)
            for record in [_tx(0x04, 3000, 1), _rx(standard), _rx(standard)]
        )
        + "\n"
    )

    result = analyze(path)

    assert result["categories"] == {
        "EXPECTED_STANDARD_RESPONSE": 1,
        "LATE_STANDARD_RESPONSE": 1,
    }
