#!/usr/bin/env python3
"""Extract representative Shine 0x20 frames and aligned Modbus snapshots.

This helper reads one or more broker JSONL logs, captures every Shine
`func=0x20` response, aligns each frame with the nearest TCP Modbus read,
then emits a curated subset of samples covering different operating states.

The resulting dataset is written to
`docs/data/shine_sample_sets.json` for use during reverse-engineering.
"""

from __future__ import annotations

import argparse
import json
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from analyze_sniff_log import Event, read_events


WORD_FIELDS: Dict[str, int] = {
    "dc_bus_voltage_raw": 3,
    "buck_boost_current_raw": 9,
    "grid_frequency_raw": 47,
    "pv_bus_voltage_raw": 21,
    "pv_bus_voltage_copy_raw": 41,
    "buck_boost_current_copy_raw": 33,
    "grid_frequency_crc_raw": 55,
    "runtime_ticks_raw": 57,
}

# Registers we aim to align with; keep the list small to avoid copying
TARGET_REGS = {
    3000, 3002, 3003, 3004, 3005, 3006, 3007, 3008, 3009,
    3025, 3092, 3093, 3171, 3173, 3174, 3175, 3176, 3179, 3181,
}


@dataclass
class ShineFrame:
    ts: float
    iso: str
    hex_payload: str
    words: List[int]


def parse_logs(paths: List[str]) -> tuple[List[ShineFrame], Dict[int, Dict[str, List[float]]]]:
    frames: List[ShineFrame] = []
    reg_series: Dict[int, Dict[str, List[float]]] = {}
    pending_reads: Dict[str, tuple[int, int]] = {}

    for ev in read_events(paths):
        if ev.func == 0x20 and ev.role == "RSP":
            data = bytes.fromhex(ev.hex)
            payload = data[3:-2]
            words = [int.from_bytes(payload[i : i + 2], "big", signed=False) for i in range(0, len(payload), 2)]
            frames.append(
                ShineFrame(
                    ts=ev.ts.timestamp(),
                    iso=ev.ts.isoformat(),
                    hex_payload=ev.hex,
                    words=words,
                )
            )
            continue

        client_from = ev.client_from or ""
        client_to = ev.client_to or ""

        if client_from.startswith("TCP:") and ev.role == "REQ" and ev.func in {3, 4}:
            try:
                raw = bytes.fromhex(ev.hex)
            except Exception:
                continue
            if len(raw) < 6:
                continue
            start = int.from_bytes(raw[2:4], "big")
            qty = int.from_bytes(raw[4:6], "big")
            pending_reads[client_from] = (start, qty)
            continue

        if client_to.startswith("TCP:") and ev.role == "RSP" and ev.func in {3, 4}:
            request = pending_reads.pop(client_to, None)
            if not request:
                continue
            start, _ = request
            try:
                raw = bytes.fromhex(ev.hex)
            except Exception:
                continue
            if len(raw) < 3:
                continue
            bytecount = raw[2]
            data = raw[3 : 3 + bytecount]
            regs = [int.from_bytes(data[i : i + 2], "big", signed=False) for i in range(0, len(data), 2)]
            ts = ev.ts.timestamp()
            for offset, value in enumerate(regs):
                reg_id = start + offset
                if reg_id not in TARGET_REGS:
                    continue
                series = reg_series.setdefault(reg_id, {"times": [], "values": []})
                series["times"].append(ts)
                series["values"].append(value)

    return frames, reg_series


def lookup_reg(series: Dict[int, Dict[str, List[float]]], reg_id: int, ts: float, *, tolerance: float = 1.5) -> Optional[int]:
    entry = series.get(reg_id)
    if not entry:
        return None
    times = entry["times"]
    values = entry["values"]
    pos = bisect_left(times, ts)
    candidates = []
    if 0 <= pos < len(times):
        candidates.append((abs(times[pos] - ts), values[pos]))
    if pos - 1 >= 0:
        candidates.append((abs(times[pos - 1] - ts), values[pos - 1]))
    if not candidates:
        return None
    diff, value = min(candidates, key=lambda x: x[0])
    if diff > tolerance:
        return None
    return value


def extract_word_subset(words: List[int]) -> Dict[str, int]:
    subset: Dict[str, int] = {}
    for name, idx in WORD_FIELDS.items():
        if idx < len(words):
            subset[name] = words[idx]
    return subset


def determine_state(regs: Dict[int, Optional[int]], words: Dict[str, int]) -> str:
    pv_current = regs.get(3006) or 0
    pv_current += regs.get(3008) or 0
    battery_current = regs.get(3174) or 0
    inverter_status = regs.get(3000)

    flags: List[str] = []
    if pv_current > 300:
        flags.append("pv_active")
    if battery_current > 5:
        flags.append("battery_charge")
    if inverter_status in {5, 6, 7, 8, 9, 10, 12}:
        flags.append("charging_mode")
    if not flags:
        flags.append("idle")
    return "+".join(sorted(set(flags)))


def bucketise(frame: ShineFrame, regs: Dict[int, Optional[int]], state: str) -> tuple[str, str, str]:
    pv_metric = regs.get(3006) or regs.get(3008) or frame.words[21] if len(frame.words) > 21 else 0
    buck_metric = regs.get(3174) or frame.words[9] if len(frame.words) > 9 else 0

    def bucket(value: int, edges: Tuple[int, ...]) -> str:
        for idx, edge in enumerate(edges):
            if value < edge:
                return f"b{idx}"
        return f"b{len(edges)}"

    pv_bucket = bucket(pv_metric, (100, 1000, 5000, 10000))
    buck_bucket = bucket(buck_metric, (5, 25, 100, 500))
    return pv_bucket, buck_bucket, state


def select_samples(frames: List[ShineFrame], series: Dict[int, Dict[str, List[float]]]) -> List[dict]:
    candidates: List[dict] = []
    for frame in frames:
        reg_snapshot: Dict[int, Optional[int]] = {
            reg: lookup_reg(series, reg, frame.ts)
            for reg in sorted(TARGET_REGS)
        }
        words_subset = extract_word_subset(frame.words)
        state = determine_state(reg_snapshot, words_subset)
        pv_bucket, buck_bucket, state_bucket = bucketise(frame, reg_snapshot, state)
        candidates.append(
            {
                "timestamp": frame.iso,
                "hex": frame.hex_payload,
                "words": words_subset,
                "registers": {str(reg): value for reg, value in reg_snapshot.items() if value is not None},
                "state": state,
                "bucket": {
                    "pv_level": pv_bucket,
                    "buck_level": buck_bucket,
                    "state": state_bucket,
                },
            }
        )

    # Group by bucket and pick at most three samples per bucket (first, middle, last)
    by_bucket: Dict[tuple[str, str, str], List[dict]] = {}
    for entry in candidates:
        key = (entry["bucket"]["pv_level"], entry["bucket"]["buck_level"], entry["bucket"]["state"])
        by_bucket.setdefault(key, []).append(entry)

    selected: List[dict] = []
    for entries in by_bucket.values():
        if len(entries) == 1:
            selected.extend(entries)
            continue
        # Preserve chronological order based on timestamp
        entries.sort(key=lambda e: e["timestamp"])
        picks = [entries[0]]
        if len(entries) > 2:
            picks.append(entries[len(entries) // 2])
        picks.append(entries[-1])
        selected.extend(picks[:3])

    # Limit overall dataset to keep it manageable
    selected.sort(key=lambda e: e["timestamp"])
    return selected[:60]


def main() -> None:
    parser = argparse.ArgumentParser(description="Assemble Shine 0x20 sample sets")
    parser.add_argument("paths", nargs="*", help="Broker JSONL log files")
    parser.add_argument(
        "--output",
        default="external/growatt-rtu-broker/docs/data/shine_sample_sets.json",
        help="Destination JSON file",
    )
    args = parser.parse_args()

    paths = args.paths if args.paths else ["external/growatt-rtu-broker/broker-260925-2.log"]
    frames, reg_series = parse_logs(paths)
    if not frames:
        raise SystemExit("No Shine 0x20 frames found in provided logs")

    samples = select_samples(frames, reg_series)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    else:
        existing = {}

    existing_sources = set(existing.get("source_logs", [])) | set(paths)
    existing_samples = existing.get("samples", [])
    existing_keys = {(item.get("timestamp"), item.get("hex")) for item in existing_samples}

    new_additions = []
    for sample in samples:
        key = (sample.get("timestamp"), sample.get("hex"))
        if key in existing_keys:
            continue
        existing_keys.add(key)
        existing_samples.append(sample)
        new_additions.append(sample)

    existing_samples.sort(key=lambda s: s.get("timestamp", ""))

    payload = {
        "source_logs": sorted(existing_sources),
        "word_fields": existing.get("word_fields", WORD_FIELDS),
        "registers": sorted(set(existing.get("registers", [])) | set(TARGET_REGS)),
        "sample_count": len(existing_samples),
        "samples": existing_samples,
    }

    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Appended {len(new_additions)} new samples to {out_path} (total: {len(existing_samples)})")


if __name__ == "__main__":
    main()
