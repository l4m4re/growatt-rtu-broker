"""Produce deterministic, metadata-only evidence from an ESP8266 firmware image.

This utility deliberately does not emulate or execute firmware.  It reports
ESP image headers, aligned candidate image headers, printable strings, and
byte-pattern locations that are useful for subsequent static review.  A
pattern hit is not promoted to protocol meaning by this tool.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path
from typing import Any

FLASH_MODES = {0: "QIO", 1: "QOUT", 2: "DIO", 3: "DOUT"}
KEYWORDS = (
    "growatt",
    "shine",
    "wifi",
    "server",
    "cloud",
    "socket",
    "http",
    "tcp",
    "udp",
    "dns",
    "ota",
    "firmware",
    "serial",
    "modbus",
    "register",
    "inverter",
    "offline",
    "online",
    "connect",
    "disconnect",
    "retry",
    "timeout",
    "error",
    "device",
    "type",
    "status",
    "reset",
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def printable_strings(data: bytes, minimum: int = 4) -> list[dict[str, Any]]:
    pattern = re.compile(rb"[ -~]{%d,}" % minimum)
    return [
        {"offset": match.start(), "text": match.group().decode("ascii")}
        for match in pattern.finditer(data)
    ]


def parse_image_header(data: bytes, offset: int) -> dict[str, Any] | None:
    """Parse the public ESP8266 image header at an aligned offset."""
    if offset < 0 or offset + 8 > len(data) or data[offset] != 0xE9:
        return None
    segment_count = data[offset + 1]
    if segment_count == 0 or segment_count > 16:
        return None
    flash_mode = data[offset + 2]
    flash_size_freq = data[offset + 3]
    entry = struct.unpack_from("<I", data, offset + 4)[0]
    cursor = offset + 8
    segments: list[dict[str, int]] = []
    for index in range(segment_count):
        if cursor + 8 > len(data):
            return None
        load_address, length = struct.unpack_from("<II", data, cursor)
        cursor += 8
        end = cursor + length
        if end > len(data):
            return None
        segments.append(
            {
                "index": index,
                "load_address": load_address,
                "length": length,
                "file_offset": cursor,
            }
        )
        cursor = end
    # ESP8266 images pad the segment payload to a 16-byte boundary before the
    # one-byte image checksum.
    checksum_offset = ((cursor + 15) // 16) * 16 - 1
    checksum = data[checksum_offset] if checksum_offset < len(data) else None
    calculated_checksum = 0xEF
    data_cursor = offset + 8
    for segment in segments:
        data_cursor += 8
        for value in data[data_cursor : data_cursor + segment["length"]]:
            calculated_checksum ^= value
        data_cursor += segment["length"]
    return {
        "offset": offset,
        "magic": "0xE9",
        "segment_count": segment_count,
        "flash_mode": FLASH_MODES.get(flash_mode, f"unknown({flash_mode})"),
        "flash_mode_value": flash_mode,
        "flash_size_frequency_value": flash_size_freq,
        "entry_point": f"0x{entry:08X}",
        "segments": segments,
        "checksum_offset": checksum_offset,
        "checksum": None if checksum is None else f"0x{checksum:02X}",
        "checksum_valid": checksum == calculated_checksum,
        "descriptor_end": cursor,
        "candidate_end": None if checksum is None else checksum_offset + 1,
    }


def aligned_headers(data: bytes) -> list[dict[str, Any]]:
    headers: list[dict[str, Any]] = []
    for offset in range(0, len(data) - 7, 0x1000):
        parsed = parse_image_header(data, offset)
        if parsed is not None:
            headers.append(parsed)
    return headers


def pattern_hits(data: bytes) -> dict[str, list[int]]:
    patterns = {
        "h43_be": bytes.fromhex("002b"),
        "h43_le": bytes.fromhex("2b00"),
        "device_type_13ec_be": bytes.fromhex("13ec"),
        "h180_be": bytes.fromhex("00b4"),
        "h188_be": bytes.fromhex("00bc"),
        "h209_be": bytes.fromhex("00d1"),
        "i3000_be": bytes.fromhex("0bb8"),
        "i3125_be": bytes.fromhex("0c35"),
        "i3250_be": bytes.fromhex("0cb2"),
        "count_100_be": bytes.fromhex("0064"),
        "count_125_be": bytes.fromhex("007d"),
        "fc03": bytes.fromhex("03"),
        "fc04": bytes.fromhex("04"),
        "fc06": bytes.fromhex("06"),
        "fc10": bytes.fromhex("10"),
        "fc20": bytes.fromhex("20"),
        "discovery_prefix": bytes.fromhex("0003002b0001"),
        "fc20_prefix": bytes.fromhex("012000000064"),
    }
    return {
        name: [match.start() for match in re.finditer(re.escape(value), data)]
        for name, value in patterns.items()
    }


def keyword_matches(strings: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result = {keyword: [] for keyword in KEYWORDS}
    for item in strings:
        lowered = item["text"].lower()
        for keyword in KEYWORDS:
            if keyword in lowered:
                result[keyword].append(item)
    return result


def analyze(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    strings = printable_strings(data)
    return {
        "path": str(path.resolve()),
        "size": len(data),
        "sha256": sha256(data),
        "leading_bytes_hex": data[:16].hex(),
        "aligned_esp8266_headers": aligned_headers(data),
        "pattern_hits": pattern_hits(data),
        "keyword_strings": keyword_matches(strings),
        "string_count": len(strings),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--json", action="store_true", help="write JSON output")
    args = parser.parse_args()
    result = {"images": [analyze(path) for path in args.images]}
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for image in result["images"]:
            print(f"{image['path']}: {image['size']} bytes, sha256={image['sha256']}")
            for header in image["aligned_esp8266_headers"]:
                print(
                    "  image @ 0x{offset:06x}: {segment_count} segments, "
                    "mode={flash_mode}, entry={entry_point}".format(**header)
                )
            print(f"  strings={image['string_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
