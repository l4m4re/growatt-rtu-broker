"""Create deterministic, sector-oriented evidence for two equal-size flash dumps."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

SECTOR_SIZE = 0x1000


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for value in data:
        counts[value] += 1
    size = len(data)
    return -sum((count / size) * math.log2(count / size) for count in counts if count)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strings(data: bytes, minimum: int = 4) -> list[str]:
    return [match.group().decode("ascii") for match in re.finditer(rb"[ -~]{%d,}" % minimum, data)]


def role(offset: int, size: int) -> tuple[str, str]:
    end = offset + size
    if offset == 0:
        return "ESP image header/primary application", "high"
    if offset < 0x70000:
        return "primary application/IROM or embedded resources", "medium"
    if offset < 0x100000:
        return "OTA/secondary-image candidate or blank space", "medium"
    if offset < 0x140000:
        return "nonblank data region; filesystem/resource candidate", "low"
    if offset >= 0x3F8000:
        return "ESP SDK/system-parameter candidate", "medium"
    return "blank/unused flash or unknown", "low" if end <= 0x3F8000 else "medium"


def sector_records(base: bytes, target: bytes) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for offset in range(0, len(base), SECTOR_SIZE):
        left = base[offset : offset + SECTOR_SIZE]
        right = target[offset : offset + SECTOR_SIZE]
        changed = sum(a != b for a, b in zip(left, right))
        if changed == 0:
            status = "same"
        else:
            status = "different"
        candidate, confidence = role(offset, len(right))
        records.append(
            {
                "start": offset,
                "end": offset + len(right),
                "length": len(right),
                "status": status,
                "changed_bytes": changed,
                "changed_percent": round(changed / len(right) * 100, 4),
                "base_entropy": round(entropy(left), 4),
                "target_entropy": round(entropy(right), 4),
                "base_strings": strings(left)[:8],
                "target_strings": strings(right)[:8],
                "candidate_role": candidate,
                "confidence": confidence,
            }
        )
    return records


def coalesced_intervals(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    intervals: list[dict[str, Any]] = []
    for record in records:
        if record["status"] == "same":
            continue
        if intervals and record["start"] == intervals[-1]["end"]:
            current = intervals[-1]
            current["end"] = record["end"]
            current["length"] += record["length"]
            current["changed_bytes"] += record["changed_bytes"]
            current["changed_percent"] = round(current["changed_bytes"] / current["length"] * 100, 4)
        else:
            intervals.append({key: record[key] for key in (
                "start", "end", "length", "changed_bytes", "changed_percent",
                "candidate_role", "confidence",
            )})
    return intervals


def analyze(base_path: Path, target_path: Path) -> dict[str, Any]:
    base = base_path.read_bytes()
    target = target_path.read_bytes()
    if len(base) != len(target):
        raise ValueError("flash images must have equal size")
    sectors = sector_records(base, target)
    return {
        "base": {"path": str(base_path.resolve()), "size": len(base), "sha256": sha256(base)},
        "target": {"path": str(target_path.resolve()), "size": len(target), "sha256": sha256(target)},
        "sector_size": SECTOR_SIZE,
        "same_bytes": sum(a == b for a, b in zip(base, target)),
        "changed_bytes": sum(a != b for a, b in zip(base, target)),
        "changed_sectors": sum(record["status"] == "different" for record in sectors),
        "coalesced_different_intervals": coalesced_intervals(sectors),
        "sectors": sectors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = analyze(args.base, args.target)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"changed={result['changed_bytes']} / {result['base']['size']} bytes")
        for interval in result["coalesced_different_intervals"]:
            print(
                f"0x{interval['start']:06x}-0x{interval['end'] - 1:06x} "
                f"{interval['length']} bytes ({interval['changed_percent']:.2f}% changed) "
                f"{interval['candidate_role']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
