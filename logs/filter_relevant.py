#!/usr/bin/env python3
"""Keep broker events useful for a long-running live diagnostic capture.

The broker writes one JSON object per line.  This filter intentionally drops
ordinary cached and physical reads, while retaining writes and their responses,
non-standard functions, unsolicited serial traffic, and failures.
"""

import json
import sys
from datetime import datetime, timezone

WRITE_EVENTS = {
    "tcp_write_acknowledged",
    "tcp_write_complete",
    "tcp_write_denied",
    "tcp_write_exception",
    "tcp_write_failed",
    "on_demand_write_forwarded",
    "shine_write_forwarded",
    "write_policy_blocked",
}

ASYNC_EVENTS = {
    "async_frame_forward_failed",
    "async_frame_forwarded",
    "async_frame_no_shine",
    "async_frame_observed",
    "inverter_unframed_bytes",
    "shine_unframed_bytes",
}

KNOWN_FUNCTIONS = {0x00, 0x03, 0x04, 0x06, 0x10, 0x20, 0xA0}
ERROR_EVENT_PARTS = (
    "error",
    "exception",
    "failed",
    "failure",
    "timeout",
    "unhandled",
    "reopen",
    "open_failed",
    "bad_crc",
    "blocked",
)


def frame_function(item: dict) -> int | None:
    value = item.get("func", item.get("function"))
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            try:
                return int(value)
            except ValueError:
                pass
    raw = item.get("hex")
    if isinstance(raw, str):
        try:
            frame = bytes.fromhex(raw)
        except ValueError:
            return None
        if len(frame) >= 2:
            return frame[1]
    return None


def event_is_error(item: dict) -> bool:
    event = item.get("event")
    role = item.get("role")
    return (
        role in {"ERROR", "WARN"}
        or (isinstance(event, str) and any(part in event for part in ERROR_EVENT_PARTS))
    )


def classify(item: dict) -> str | None:
    event = item.get("event")
    function = frame_function(item)
    is_write = bool(item.get("is_write")) or function in (0x06, 0x10)

    if event in ASYNC_EVENTS:
        return "async_error" if event_is_error(item) else "async"
    if event in WRITE_EVENTS or is_write:
        return "write_error" if event_is_error(item) else "write"
    if function in (0x20, 0xA0):
        return "fc20_error" if function == 0xA0 or event_is_error(item) else "fc20"
    if function is not None and function not in KNOWN_FUNCTIONS:
        return "unknown_function_error" if event_is_error(item) else "unknown_function"
    if event_is_error(item):
        return "error"
    return None


print(
    json.dumps(
        {
            "event": "relevant_capture_started",
            "capture_class": "metadata",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "scope": [
                "writes_and_responses",
                "fc20",
                "unknown_functions",
                "async_frames",
                "errors",
            ],
        },
        separators=(",", ":"),
    ),
    flush=True,
)

for line in sys.stdin:
    try:
        item = json.loads(line)
    except (TypeError, ValueError):
        continue
    if not isinstance(item, dict):
        continue
    capture_class = classify(item)
    if capture_class is None:
        continue
    item["capture_class"] = capture_class
    print(json.dumps(item, separators=(",", ":"), sort_keys=True), flush=True)
