"""Search a Shine flash dump for serial and identity encodings, read-only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def all_offsets(data: bytes, needle: bytes) -> list[int]:
    result: list[int] = []
    start = 0
    while needle:
        offset = data.find(needle, start)
        if offset < 0:
            return result
        result.append(offset)
        start = offset + 1
    return result


def candidates(serial: str) -> dict[str, bytes]:
    raw = serial.encode("ascii")
    lower = serial.lower().encode("ascii")
    upper = serial.upper().encode("ascii")
    result = {
        "ascii": raw,
        "ascii_nul": raw + b"\0",
        "ascii_lower": lower,
        "ascii_upper": upper,
        "utf16le": serial.encode("utf-16le"),
        "utf16be": serial.encode("utf-16be"),
    }
    for width in (8, 10, 12, 16, 20, 32):
        if width >= len(raw):
            for name, fill in (("nul", b"\0"), ("space", b" "), ("ff", b"\xff")):
                result[f"fixed_{width}_{name}"] = raw + fill * (width - len(raw))
    return result


def _hex_bytes(value: str, *, expected_lengths: tuple[int, ...]) -> bytes:
    normalized = value.replace(":", "").replace("-", "").removeprefix("0x")
    if len(normalized) not in expected_lengths:
        expected = ", ".join(str(length) for length in expected_lengths)
        raise ValueError(f"hex value must contain {expected} hex digits")
    try:
        return bytes.fromhex(normalized)
    except ValueError as err:
        raise ValueError("hex value contains a non-hexadecimal character") from err


def mac_candidates(mac: str) -> dict[str, bytes]:
    raw = _hex_bytes(mac, expected_lengths=(12,))
    compact_lower = raw.hex().encode("ascii")
    compact_upper = raw.hex().upper().encode("ascii")
    colon_lower = ":".join(f"{value:02x}" for value in raw).encode("ascii")
    colon_upper = ":".join(f"{value:02X}" for value in raw).encode("ascii")
    dash_lower = "-".join(f"{value:02x}" for value in raw).encode("ascii")
    dash_upper = "-".join(f"{value:02X}" for value in raw).encode("ascii")
    reversed_raw = raw[::-1]
    return {
        "mac_raw": raw,
        "mac_raw_reversed": reversed_raw,
        "mac_hex_lower": compact_lower,
        "mac_hex_upper": compact_upper,
        "mac_colon_lower": colon_lower,
        "mac_colon_upper": colon_upper,
        "mac_dash_lower": dash_lower,
        "mac_dash_upper": dash_upper,
    }


def chip_id_candidates(chip_id: str) -> dict[str, bytes]:
    value = int(chip_id, 0)
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError("chip ID must fit in 32 bits")
    raw32 = value.to_bytes(4, "big")
    raw24 = (value & 0xFFFFFF).to_bytes(3, "big")
    decimal = str(value).encode("ascii")
    return {
        "chip_id_32_be": raw32,
        "chip_id_32_le": raw32[::-1],
        "chip_id_24_be": raw24,
        "chip_id_24_le": raw24[::-1],
        "chip_id_hex_8_lower": f"{value:08x}".encode("ascii"),
        "chip_id_hex_8_upper": f"{value:08X}".encode("ascii"),
        "chip_id_hex_6_lower": f"{value & 0xFFFFFF:06x}".encode("ascii"),
        "chip_id_hex_6_upper": f"{value & 0xFFFFFF:06X}".encode("ascii"),
        "chip_id_decimal": decimal,
    }


def region(offset: int) -> str:
    if offset < 0x70000:
        return "primary application/IROM or embedded resources"
    if offset < 0x100000:
        return "OTA/secondary-image candidate or blank space"
    if offset < 0x140000:
        return "nonblank data region; filesystem/resource candidate"
    if offset >= 0x3F8000:
        return "ESP SDK/system-parameter candidate"
    return "unknown/unused"


def analyze(
    path: Path,
    serial: str,
    context: int = 16,
    *,
    mac: str | None = None,
    chip_id: str | None = None,
) -> dict[str, Any]:
    data = path.read_bytes()
    hits: list[dict[str, Any]] = []
    search_sets = {"serial": candidates(serial)}
    if mac is not None:
        search_sets["mac"] = mac_candidates(mac)
    if chip_id is not None:
        search_sets["chip_id"] = chip_id_candidates(chip_id)
    for search_kind, search_candidates in search_sets.items():
        for encoding, needle in search_candidates.items():
            for offset in all_offsets(data, needle):
                start = max(0, offset - context)
                end = min(len(data), offset + len(needle) + context)
                hits.append({
                    "search_kind": search_kind,
                    "encoding": encoding,
                    "offset": offset,
                    "length": len(needle),
                    "hex_context": data[start:end].hex(),
                    "printable_context": "".join(chr(value) if 32 <= value < 127 else "." for value in data[start:end]),
                    "candidate_region": region(offset),
                })
    partials = {token: all_offsets(data, token.encode("ascii")) for token in ("XGD", "xgd", "CCN", "ccn")}
    return {
        "path": str(path.resolve()),
        "size": len(data),
        "serial": serial,
        "mac": mac,
        "chip_id": chip_id,
        "exact_or_encoded_hits": hits,
        "partial_token_hits": partials,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--serial", default="XGD6CCN109")
    parser.add_argument("--mac", help="MAC address to search in raw and common text forms")
    parser.add_argument("--chip-id", help="ESP8266 chip ID, for example 0x0074f8de")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = analyze(args.image, args.serial, mac=args.mac, chip_id=args.chip_id)
    except ValueError as err:
        parser.error(str(err))
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"exact_or_encoded_hits={len(result['exact_or_encoded_hits'])}")
        for token, hits in result["partial_token_hits"].items():
            print(f"{token}: {[f'0x{offset:x}' for offset in hits]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
