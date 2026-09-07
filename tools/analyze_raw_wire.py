#!/usr/bin/env python3
"""Reconstruct bounded Shine raw-wire conversations without guessing associations.

The raw-transparent broker mode records byte chunks, not Modbus transactions.
This analyser uses direction runs, structurally determined lengths and Modbus
CRC validation to recover complete requests and responses.  It deliberately
does not scan arbitrary offsets for CRC-valid data: bytes which do not parse at
the current structural boundary remain unclassified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


FUNCTION_NAMES = {3: "FC03", 4: "FC04", 6: "FC06", 16: "FC10", 32: "FC20"}
KNOWN_FUNCTIONS = set(FUNCTION_NAMES)
RETRY_MAX_INTERVAL_S = 5.0


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def crc_valid(frame: bytes) -> bool:
    if len(frame) < 4:
        return False
    crc = 0xFFFF
    for value in frame[:-2]:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return (crc & 0xFFFF) == int.from_bytes(frame[-2:], "little")


@dataclass(frozen=True)
class WireEvent:
    ts: datetime
    direction: str
    raw: bytes


@dataclass
class Request:
    ts: datetime
    raw: bytes
    unit: int
    function: int
    start: int | None = None
    count: int | None = None
    value: int | None = None
    response_index: int | None = None
    interval_s: float | None = None
    classification: str = "healthy_steady_state"


@dataclass
class Response:
    first_ts: datetime
    last_ts: datetime
    raw: bytes
    unit: int
    function: int
    crc_ok: bool
    request_index: int | None = None


def _request_from_frame(ts: datetime, frame: bytes) -> Request | None:
    if len(frame) != 8 or not crc_valid(frame) or frame[1] not in KNOWN_FUNCTIONS:
        return None
    function = frame[1]
    request = Request(ts, frame, frame[0], function)
    if function in (3, 4, 32):
        request.start = int.from_bytes(frame[2:4], "big")
        request.count = int.from_bytes(frame[4:6], "big")
    elif function == 6:
        request.start = int.from_bytes(frame[2:4], "big")
        request.value = int.from_bytes(frame[4:6], "big")
    return request


def _response_frames(events: list[WireEvent]) -> tuple[list[Response], int]:
    """Parse one inverter-to-Shine direction run conservatively."""
    data = b"".join(event.raw for event in events)
    responses: list[Response] = []
    unknown = 0
    offset = 0
    while offset < len(data):
        if offset + 3 > len(data):
            unknown += len(data) - offset
            break
        function = data[offset + 1]
        if function not in (3, 4, 32):
            unknown += 1
            offset += 1
            continue
        byte_count = data[offset + 2]
        end = offset + 5 + byte_count
        if byte_count % 2 or end > len(data):
            unknown += 1
            offset += 1
            continue
        frame = data[offset:end]
        if not crc_valid(frame):
            unknown += 1
            offset += 1
            continue
        responses.append(
            Response(
                events[0].ts,
                events[-1].ts,
                frame,
                frame[0],
                function,
                True,
            )
        )
        offset = end
    return responses, unknown


def _request_frames(events: list[WireEvent]) -> tuple[list[Request], int]:
    requests: list[Request] = []
    unknown = 0
    for event in events:
        # Raw Shine requests observed in this mode are complete 8-byte RTU
        # requests.  Do not mine boot/debug bytes for accidental subframes.
        request = _request_from_frame(event.ts, event.raw)
        if request is None:
            unknown += len(event.raw)
        else:
            requests.append(request)
    return requests, unknown


def _percentiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {
            key: None
            for key in ("min", "p05", "p25", "median", "p75", "p95", "p99", "max")
        }
    ordered = sorted(values)

    def percentile(rank: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        position = (len(ordered) - 1) * rank
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    return {
        "min": ordered[0],
        "p05": percentile(0.05),
        "p25": percentile(0.25),
        "median": statistics.median(ordered),
        "p75": percentile(0.75),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "max": ordered[-1],
    }


def _word_statistics(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index in range(100):
        values = [frame["payload_words"][index] for frame in frames]
        signed = [value if value < 0x8000 else value - 0x10000 for value in values]
        result.append(
            {
                "word": index,
                "values_hex": [f"0x{value:04X}" for value in values],
                "values_uint16": values,
                "values_int16": signed,
                "min_uint16": min(values),
                "max_uint16": max(values),
                "unique_count": len(set(values)),
                "all_zero": all(value == 0 for value in values),
                "sign_changes": any(value >= 0x8000 for value in values)
                and any(value < 0x8000 for value in values),
                "classification": (
                    "zero"
                    if all(value == 0 for value in values)
                    else "constant"
                    if len(set(values)) == 1
                    else "changing_signed"
                    if any(value >= 0x8000 for value in values)
                    else "changing"
                ),
            }
        )
    return result


def _u32(value_a: int, value_b: int) -> int:
    return (value_a << 16) | value_b


def _adjacent_candidates(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index in range(99):
        values = [
            _u32(frame["payload_words"][index], frame["payload_words"][index + 1])
            for frame in frames
        ]
        signed = [
            value if value < 0x80000000 else value - 0x100000000 for value in values
        ]
        result.append(
            {
                "first_word": index,
                "second_word": index + 1,
                "u32_be": values,
                "s32_be": signed,
                "u32_unique_count": len(set(values)),
                "changes": len(set(values)) > 1,
            }
        )
    return result


def _request_dict(request: Request) -> dict[str, Any]:
    return {
        "timestamp": request.ts.isoformat(),
        "raw_hex": request.raw.hex(),
        "length_bytes": len(request.raw),
        "unit": request.unit,
        "function": request.function,
        "function_hex": f"0x{request.function:02X}",
        "start": request.start,
        "count": request.count,
        "value": request.value,
        "crc_ok": crc_valid(request.raw),
        "interval_s": request.interval_s,
        "classification": request.classification,
        "response_index": request.response_index,
    }


def _response_dict(response: Response) -> dict[str, Any]:
    byte_count = response.raw[2] if len(response.raw) >= 3 else None
    payload = response.raw[3:-2] if len(response.raw) >= 5 else b""
    return {
        "first_timestamp": response.first_ts.isoformat(),
        "last_chunk_timestamp": response.last_ts.isoformat(),
        "raw_hex": response.raw.hex(),
        "length_bytes": len(response.raw),
        "unit": response.unit,
        "function": response.function,
        "function_hex": f"0x{response.function:02X}",
        "byte_count": byte_count,
        "returned_word_count": len(payload) // 2 if len(payload) % 2 == 0 else None,
        "crc_ok": response.crc_ok,
        "request_index": response.request_index,
    }


def analyze(path: Path) -> dict[str, Any]:
    events: list[WireEvent] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("event") != "shine_wire":
            continue
        try:
            raw = bytes.fromhex(record["hex"])
            direction = record["direction"]
            ts = parse_ts(record["ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if direction in ("shine_to_inverter", "inverter_to_shine"):
            events.append(WireEvent(ts, direction, raw))

    runs: list[list[WireEvent]] = []
    for event in events:
        if not runs or runs[-1][0].direction != event.direction:
            runs.append([event])
        else:
            runs[-1].append(event)

    requests: list[Request] = []
    responses: list[Response] = []
    unknown_bytes = 0
    for run in runs:
        if run[0].direction == "shine_to_inverter":
            parsed, unknown = _request_frames(run)
            requests.extend(parsed)
        else:
            parsed, unknown = _response_frames(run)
            responses.extend(parsed)
        unknown_bytes += unknown

    requests.sort(key=lambda request: request.ts)
    responses.sort(key=lambda response: response.first_ts)
    previous: Request | None = None
    for index, request in enumerate(requests):
        if previous is not None:
            request.interval_s = (request.ts - previous.ts).total_seconds()
        if request.unit == 0 and request.start == 43 and request.count == 1:
            request.classification = (
                "startup_discovery" if previous is None else "retry"
            )
        elif (
            previous is not None
            and request.raw == previous.raw
            and previous.response_index is None
            and request.interval_s is not None
            and request.interval_s <= RETRY_MAX_INTERVAL_S
        ):
            request.classification = "retry"
        previous = request

    for response in responses:
        eligible = [
            (index, request)
            for index, request in enumerate(requests)
            if request.response_index is None
            and request.ts <= response.first_ts
            and request.unit == response.unit
            and request.function == response.function
        ]
        if not eligible:
            continue
        index, request = eligible[-1]
        if request.count is not None and response.function in (3, 4, 32):
            expected_words = request.count
            returned_words = (len(response.raw) - 5) // 2
            if expected_words != returned_words:
                continue
        request.response_index = responses.index(response)
        response.request_index = index

    # A request's retry classification depends on the association pass.
    previous = None
    for request in requests:
        if request.unit == 0 and request.start == 43 and request.count == 1:
            request.classification = (
                "startup_discovery" if previous is None else "retry"
            )
        elif (
            previous is not None
            and request.raw == previous.raw
            and previous.response_index is None
            and request.interval_s is not None
            and request.interval_s <= RETRY_MAX_INTERVAL_S
        ):
            request.classification = "retry"
        previous = request

    standard_polls: list[dict[str, Any]] = []
    for request_index, request in enumerate(requests):
        if request.function not in (3, 4) or request.response_index is None:
            continue
        response = responses[request.response_index]
        payload = response.raw[3:-2]
        words = [
            int.from_bytes(payload[i : i + 2], "big") for i in range(0, len(payload), 2)
        ]
        standard_polls.append(
            {
                "request_index": request_index,
                "table": "holding" if request.function == 3 else "input",
                "function": request.function,
                "start": request.start,
                "count": request.count,
                "timestamp": response.first_ts.isoformat(),
                "register_words": {
                    str(request.start + offset): value
                    for offset, value in enumerate(words)
                },
            }
        )

    fc20_frames: list[dict[str, Any]] = []
    for request_index, request in enumerate(requests):
        if request.function != 32 or request.response_index is None:
            continue
        response = responses[request.response_index]
        payload = response.raw[3:-2]
        if len(payload) != 200:
            continue
        words = [int.from_bytes(payload[i : i + 2], "big") for i in range(0, 200, 2)]
        fc20_frames.append(
            {
                "request_index": request_index,
                "request_timestamp": request.ts.isoformat(),
                "response_timestamp": response.first_ts.isoformat(),
                "response_latency_s": (response.first_ts - request.ts).total_seconds(),
                "request_hex": request.raw.hex(),
                "response_hex": response.raw.hex(),
                "payload_length_bytes": len(payload),
                "payload_word_count": len(words),
                "payload_words": words,
            }
        )

    intervals = [
        request.interval_s for request in requests if request.interval_s is not None
    ]
    fc20_standard: list[dict[str, Any]] = []
    for frame in fc20_frames:
        frame_ts = parse_ts(frame["request_timestamp"])
        nearby = [
            poll
            for poll in standard_polls
            if 0 <= (parse_ts(poll["timestamp"]) - frame_ts).total_seconds() <= 2.1
        ]
        fc20_standard.append(
            {
                "fc20_request_index": frame["request_index"],
                "contemporaneous_standard_poll_request_indices": [
                    poll["request_index"] for poll in nearby
                ],
                "note": "Proximity is reported for review; it does not promote a FC20 word to a semantic register.",
            }
        )

    first_ts = events[0].ts.isoformat() if events else None
    last_ts = events[-1].ts.isoformat() if events else None
    return {
        "schema": "growatt-raw-wire-analysis-v1",
        "analysis": {
            "source": "LIVE DEVICE raw-transparent WIRE capture",
            "parser": "tools/analyze_raw_wire.py",
            "association_policy": "direction + structural length + CRC + unit/function; unresolved associations are retained",
            "unknown_bytes": unknown_bytes,
        },
        "capture": {
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "recorded_wire_events": len(events),
            "first_timestamp": first_ts,
            "last_timestamp": last_ts,
        },
        "summary": {
            "shine_to_inverter_chunks": sum(
                1 for event in events if event.direction == "shine_to_inverter"
            ),
            "inverter_to_shine_chunks": sum(
                1 for event in events if event.direction == "inverter_to_shine"
            ),
            "requests": len(requests),
            "responses": len(responses),
            "matched_requests": sum(
                request.response_index is not None for request in requests
            ),
            "unmatched_requests": sum(
                request.response_index is None for request in requests
            ),
            "unmatched_responses": sum(
                response.request_index is None for response in responses
            ),
            "request_classifications": {
                classification: sum(
                    request.classification == classification for request in requests
                )
                for classification in (
                    "startup_discovery",
                    "healthy_steady_state",
                    "retry",
                )
            },
            "function_request_counts": {
                FUNCTION_NAMES[function]: sum(
                    request.function == function for request in requests
                )
                for function in sorted({request.function for request in requests})
            },
            "response_lengths": {
                str(length): sum(len(response.raw) == length for response in responses)
                for length in sorted({len(response.raw) for response in responses})
            },
        },
        "requests": [_request_dict(request) for request in requests],
        "responses": [_response_dict(response) for response in responses],
        "standard_polls": standard_polls,
        "fc20": {
            "exact_request_hex": sorted(
                {frame["request_hex"] for frame in fc20_frames}
            ),
            "frames": fc20_frames,
            "word_statistics": _word_statistics(fc20_frames) if fc20_frames else [],
            "adjacent_32bit_candidates": _adjacent_candidates(fc20_frames)
            if fc20_frames
            else [],
            "standard_correlations": fc20_standard,
            "structure": {
                "response_length_bytes": 205,
                "header_and_byte_count": "unit + function + byte_count = 01 20 c8",
                "payload_length_bytes": 200,
                "payload_word_count": 100,
                "crc_length_bytes": 2,
            },
        },
        "timing": {
            "request_intervals_s": intervals,
            "request_interval_statistics_s": _percentiles(intervals),
            "response_latency_s": [
                (
                    responses[request.response_index].first_ts - request.ts
                ).total_seconds()
                for request in requests
                if request.response_index is not None
            ],
            "vendor_reference": {
                "minimum_cmd_period_ms": 850,
                "recommended_cmd_period_ms": 1000,
                "interpretation": "V1.24 vendor protocol characteristic; not a broker-only throttle",
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--json", type=Path, help="write the machine-readable analysis")
    args = parser.parse_args()
    result = analyze(args.capture)
    if args.json:
        args.json.write_text(json.dumps(result, indent=2) + "\n")
    else:
        print(json.dumps(result["summary"], indent=2))
        print(json.dumps(result["timing"], indent=2))


if __name__ == "__main__":
    main()
