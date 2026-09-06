#!/usr/bin/env python3
"""Conservatively classify a bounded broker physical RX forensic capture.

The capture is intentionally treated as an asynchronous byte stream.  This
tool only calls a frame standard when its length is structurally determined
and its Modbus CRC is valid.  Bytes that do not meet that contract remain
UNKNOWN_BYTES; the tool does not force arbitrary CRC-valid substrings into
frames.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


KNOWN_FUNCTIONS = {0x03, 0x04, 0x06, 0x10, 0x20}
STANDARD_FUNCTIONS = {0x03, 0x04, 0x06, 0x10}


def crc_ok(frame: bytes) -> bool:
    if len(frame) < 4:
        return False
    crc = 0xFFFF
    for value in frame[:-2]:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return (crc & 0xFFFF) == int.from_bytes(frame[-2:], "little")


def expected_length(request: bytes) -> tuple[int, int, int | None] | None:
    if len(request) < 8:
        return None
    unit, function = request[:2]
    if function in (0x03, 0x04):
        count = int.from_bytes(request[4:6], "big")
        return unit, function, 5 + count * 2
    if function in (0x06, 0x10):
        return unit, function, 8
    return None


@dataclass
class Tx:
    ts: str
    unit: int
    function: int
    expected_len: int | None
    used: bool = False


@dataclass
class Candidate:
    ts: str
    source: str
    raw: bytes
    function: int | None
    crc_valid: bool
    category: str
    relation: str
    structure: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "source": self.source,
            "length": len(self.raw),
            "hex": self.raw.hex(),
            "unit": self.raw[0] if self.raw else None,
            "function": self.function,
            "function_hex": (
                f"0x{self.function:02X}" if self.function is not None else None
            ),
            "crc_valid": self.crc_valid,
            "category": self.category,
            "relation": self.relation,
            "structure": self.structure,
        }


def _structured_end(data: bytes, start: int) -> tuple[int | None, str]:
    """Return an end offset only for an unambiguous known frame shape."""
    if start + 2 > len(data):
        return None, "partial_header"
    function = data[start + 1]
    remaining = len(data) - start

    if function in (0x03, 0x04, 0x20):
        if remaining < 3:
            return None, "partial_byte_count"
        byte_count = data[start + 2]
        if byte_count > 250:
            return None, "invalid_byte_count"
        end = start + 5 + byte_count
        return (end, "byte_count") if end <= len(data) else (None, "partial_frame")
    if function == 0x06:
        return (start + 8, "fixed_8") if remaining >= 8 else (None, "partial_frame")
    if function == 0x10:
        if remaining >= 8:
            return start + 8, "fixed_8"
        return None, "partial_frame"
    return None, "unknown_function"


def _extract_known(data: bytes) -> tuple[list[tuple[int, bytes, int, str]], int]:
    """Extract structurally bounded candidates and count unclassified bytes."""
    candidates: list[tuple[int, bytes, int, str]] = []
    unknown = 0
    pos = 0
    while pos < len(data):
        if pos + 2 > len(data):
            unknown += len(data) - pos
            break
        end, structure = _structured_end(data, pos)
        if end is None:
            unknown += 1
            pos += 1
            continue
        frame = data[pos:end]
        if crc_ok(frame):
            candidates.append((pos, frame, data[pos + 1], structure))
            pos = end
            continue
        unknown += 1
        pos += 1
    return candidates, unknown


def _parse_tx(record: dict[str, Any]) -> Tx | None:
    try:
        raw = bytes.fromhex(record["hex"])
        spec = expected_length(raw)
    except (KeyError, TypeError, ValueError):
        return None
    if spec is None:
        return None
    unit, function, length = spec
    return Tx(record.get("ts", ""), unit, function, length)


def _match_tx(candidate: Candidate, transactions: list[Tx]) -> tuple[str, Tx | None]:
    if not candidate.raw or candidate.function is None:
        return "none", None
    unit = candidate.raw[0]
    length = len(candidate.raw)
    matching = [
        tx
        for tx in transactions
        if not tx.used
        and tx.unit == unit
        and tx.function == candidate.function
        and (tx.expected_len is None or tx.expected_len == length)
    ]
    if matching:
        tx = matching[-1]
        tx.used = True
        return "expected", tx
    prior = [
        tx
        for tx in transactions
        if tx.unit == unit and tx.function == candidate.function
        and (tx.expected_len is None or tx.expected_len == length)
    ]
    return ("late", prior[-1]) if prior else ("none", None)


def analyze(path: Path) -> dict[str, Any]:
    records = [
        json.loads(line) for line in path.read_text().splitlines() if line.strip()
    ]
    transactions = [
        tx
        for record in records
        if record.get("event") == "physical_tx"
        and (tx := _parse_tx(record)) is not None
    ]
    seen_transactions: list[Tx] = []
    candidates: list[Candidate] = []
    unknown_bytes = 0
    captured_bytes = 0

    rx_group: list[dict[str, Any]] = []

    def classify_group(group: list[dict[str, Any]]) -> None:
        nonlocal unknown_bytes, captured_bytes
        if not group:
            return
        chunks: list[bytes] = []
        for record in group:
            try:
                data = bytes.fromhex(record.get("hex", ""))
            except ValueError:
                unknown_bytes += int(record.get("captured_length", 0))
                continue
            chunks.append(data)
            captured_bytes += len(data)
        if not chunks:
            return
        data = b"".join(chunks)
        extracted, unknown = _extract_known(data)
        unknown_bytes += unknown
        first = group[0]
        for _offset, raw, function, structure in extracted:
            candidate = Candidate(
                ts=first.get("ts", ""),
                source=first.get("source", ""),
                raw=raw,
                function=function,
                crc_valid=True,
                category="",
                relation="",
                structure=structure,
            )
            relation, _tx = _match_tx(candidate, seen_transactions)
            candidate.relation = relation
            if relation == "expected":
                candidate.category = "EXPECTED_STANDARD_RESPONSE"
            elif relation == "late":
                candidate.category = "LATE_STANDARD_RESPONSE"
            else:
                candidate.category = "UNSOLICITED_VALID_CRC_FRAME"
            candidates.append(candidate)

    for record in records:
        if record.get("event") == "physical_tx":
            classify_group(rx_group)
            rx_group = []
            transaction = _parse_tx(record)
            if transaction is not None:
                seen_transactions.append(transaction)
            continue
        if (
            record.get("event") == "physical_rx_raw"
            and record.get("kind") == "serial_read"
            and record.get("captured_bytes")
        ):
            rx_group.append(record)
    classify_group(rx_group)

    by_function: dict[str, dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    lengths: defaultdict[str, Counter[int]] = defaultdict(Counter)
    expected_counts: Counter[str] = Counter()
    unsolicited_counts: Counter[str] = Counter()
    crc_counts: Counter[str] = Counter()
    for candidate in candidates:
        key = str(candidate.function)
        counts[key] += 1
        lengths[key][len(candidate.raw)] += 1
        crc_counts[key] += int(candidate.crc_valid)
        expected_counts[key] += int(candidate.category == "EXPECTED_STANDARD_RESPONSE")
        unsolicited_counts[key] += int(
            candidate.category == "UNSOLICITED_VALID_CRC_FRAME"
        )
    for function in sorted(counts, key=int):
        by_function[function] = {
            "decimal": int(function),
            "hex": f"0x{int(function):02X}",
            "total": counts[function],
            "expected_response": expected_counts[function],
            "unsolicited": unsolicited_counts[function],
            "crc_valid": crc_counts[function],
            "lengths": dict(sorted(lengths[function].items())),
        }

    category_counts = Counter(candidate.category for candidate in candidates)
    return {
        "capture": str(path),
        "records": len(records),
        "physical_tx_records": len(transactions),
        "raw_rx_bytes": captured_bytes,
        "candidate_frames": len(candidates),
        "unknown_bytes": unknown_bytes,
        "categories": dict(sorted(category_counts.items())),
        "function_codes": by_function,
        "fc_0x20_observed": any(candidate.function == 0x20 for candidate in candidates),
        "candidates": [candidate.as_dict() for candidate in candidates],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    args = parser.parse_args()
    result = analyze(args.capture)
    if args.json:
        print(json.dumps(result, indent=2))
        return
    print(f"capture={result['capture']}")
    print(f"raw_rx_bytes={result['raw_rx_bytes']}")
    print(f"candidate_frames={result['candidate_frames']}")
    print(f"unknown_bytes={result['unknown_bytes']}")
    print(f"fc_0x20_observed={'YES' if result['fc_0x20_observed'] else 'NO'}")
    print("categories:", json.dumps(result["categories"], sort_keys=True))
    print("function_codes:")
    for entry in result["function_codes"].values():
        print(json.dumps(entry, sort_keys=True))


if __name__ == "__main__":
    main()
