#!/usr/bin/env python3
"""
Analyze Growatt broker sniff logs (JSONL) to detect unsuccessful transfers, timeouts,
CRC errors, and potential frame detection issues (combined frames).

Usage:
  python tools/analyze_sniff_log.py /path/to/broker-*.log [--client SHINE] [--since 2025-09-21T00:00:00]

Input format:
  Each line is a JSON object as emitted by the broker's EventHub/WireLogger, e.g.:
    {"ts": "2025-09-21T12:34:56.789", "role": "REQ", "from_client": "SHINE", "crc_ok": true, "hex": "...", "uid":1, "func":16, ...}
    {"ts": "2025-09-21T12:34:56.912", "role": "RSP", "to_client": "SHINE", "crc_ok": true, "hex": "...", ...}
    {"ts": "...", "event": "downstream_timeout", "from_client":"SHINE", "timeout":1.5}

What it reports:
  - Summary counts per client (REQ/RSP, timeouts, DROPs, serial errors)
  - Sequences of repeated timeouts (likely failed transfers)
  - RSP/REQ with crc_ok = false
  - Suspected combined frames: a single hex payload that contains multiple valid RTU subframes
  - Uncommon function codes, large payloads (e.g., write-multiple with big byte counts)

You can also pipe the live sniff feed into this tool:
  nc <host> 5700 | python tools/analyze_sniff_log.py -
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ISO_FMT = "%Y-%m-%dT%H:%M:%S.%f"
ISO_ALT = "%Y-%m-%dT%H:%M:%S"


def parse_ts(s: str) -> datetime:
    # Accept "YYYY-mm-ddTHH:MM:SS(.mmm)" (millis precision) or seconds-only
    try:
        # handle "2025-09-21T12:34:56.789"
        if "." not in s:
            return datetime.strptime(s, ISO_ALT).replace(tzinfo=timezone.utc)
        # normalize microseconds length
        head, tail = s.split(".", 1)
        micros = (tail + "000000")[:6]
        return datetime.strptime(head + "." + micros, ISO_FMT).replace(
            tzinfo=timezone.utc
        )
    except Exception:
        # Fallback: try fromisoformat; if naive, assume UTC
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class Event:
    ts: datetime
    raw: Dict[str, Any]

    @property
    def role(self) -> Optional[str]:
        return self.raw.get("role")

    @property
    def event(self) -> Optional[str]:
        return self.raw.get("event")

    @property
    def client_from(self) -> Optional[str]:
        return self.raw.get("from_client")

    @property
    def client_to(self) -> Optional[str]:
        return self.raw.get("to_client")

    @property
    def func(self) -> Optional[int]:
        v = self.raw.get("func")
        try:
            return int(v) if v is not None else None
        except Exception:
            return None

    @property
    def uid(self) -> Optional[int]:
        v = self.raw.get("uid")
        try:
            return int(v) if v is not None else None
        except Exception:
            return None

    @property
    def hex(self) -> str:
        return self.raw.get("hex", "")

    @property
    def crc_ok(self) -> Optional[bool]:
        v = self.raw.get("crc_ok")
        if isinstance(v, bool):
            return v
        return None

    @property
    def body_len(self) -> Optional[int]:
        v = self.raw.get("len")
        try:
            return int(v) if v is not None else None
        except Exception:
            return None

    @property
    def total_len(self) -> int:
        # Includes address+func+CRC if hex present
        return len(self.hex) // 2


@dataclass
class ClientStats:
    req: int = 0
    rsp: int = 0
    timeouts: int = 0
    drops: int = 0
    serial_errors: int = 0
    crc_bad: int = 0
    unknown_func: int = 0
    large_frames: int = 0

    # For sequences
    last_req_ts: Optional[datetime] = None
    timeout_streak: int = 0


KNOWN_FUNCS = {0x03, 0x04, 0x06, 0x10}


def is_suspect_large(ev: Event, threshold: int = 256) -> bool:
    return ev.total_len >= threshold


def scan_combined_frames(data: bytes, *, stop_after: int = 2) -> int:
    """Heuristic: count valid RTU subframes within a single blob.
    Looks for [..payload..][crc_lo][crc_hi] boundaries that validate.
    Returns how many valid subframes are found (>=1). Stops early after `stop_after`.
    """
    n = len(data)
    if n < 4:
        return 0

    def modbus_crc(buf: bytes) -> int:
        crc = 0xFFFF
        for b in buf:
            crc ^= b
            for _ in range(8):
                crc = (crc >> 1) ^ 0xA001 if (crc & 1) else (crc >> 1)
        return crc & 0xFFFF

    count = 0
    # Minimum RTU frame is 4 bytes
    for end in range(4, n + 1):
        body = data[: end - 2]
        crc_bytes = data[end - 2 : end]
        if modbus_crc(body) == int.from_bytes(crc_bytes, "little"):
            count += 1
            if count >= stop_after:
                break
    return count


def read_events(
    paths: List[str],
    *,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit_lines: Optional[int] = None,
) -> Iterable[Event]:
    if not paths:
        yield from ()
        return

    def _yield(obj: Dict[str, Any]):
        ts_str = obj.get("ts")
        if ts_str is None:
            ts = datetime.now(timezone.utc)
        else:
            ts = parse_ts(ts_str)
        if since and ts < since:
            return
        if until and ts > until:
            return
        yield Event(ts=ts, raw=obj)

    yielded = 0
    if len(paths) == 1 and paths[0] in ("-", "/dev/stdin"):
        for line in sys.stdin:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            for ev in _yield(obj):
                yield ev
                yielded += 1
                if limit_lines and yielded >= limit_lines:
                    return
        return
    for p in paths:
        with Path(p).open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                for ev in _yield(obj):
                    yield ev
                    yielded += 1
                    if limit_lines and yielded >= limit_lines:
                        return


def analyze(
    paths: List[str],
    *,
    client_filter: Optional[str] = None,
    detect_combined: bool = True,
    combined_threshold: int = 64,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit_lines: Optional[int] = None,
) -> None:
    stats: Dict[str, ClientStats] = {}
    suspects: List[str] = []

    def get_stats(name: str) -> ClientStats:
        if name not in stats:
            stats[name] = ClientStats()
        return stats[name]

    for ev in read_events(paths, since=since, until=until, limit_lines=limit_lines):
        # Determine client label for accounting
        client = ev.client_from or ev.client_to or "(unknown)"
        if client_filter and client != client_filter:
            continue
        cs = get_stats(client)

        # Role accounting
        if ev.role == "REQ":
            cs.req += 1
            cs.last_req_ts = ev.ts
            # Heuristics: uncommon function or very large frame
            if ev.func is not None and ev.func not in KNOWN_FUNCS:
                cs.unknown_func += 1
                suspects.append(
                    f"{ev.ts} {client} REQ unknown func={ev.func} len={ev.total_len}"
                )
            if is_suspect_large(ev):
                cs.large_frames += 1
                suspects.append(
                    f"{ev.ts} {client} REQ large frame {ev.total_len}B func={ev.func}"
                )
        elif ev.role == "RSP":
            cs.rsp += 1
            # Bad CRC on a response
            if ev.crc_ok is False:
                cs.crc_bad += 1
                suspects.append(f"{ev.ts} {client} RSP bad CRC len={ev.total_len}")
            if is_suspect_large(ev):
                cs.large_frames += 1
                suspects.append(
                    f"{ev.ts} {client} RSP large frame {ev.total_len}B func={ev.func}"
                )
            if detect_combined and ev.total_len >= combined_threshold:
                try:
                    data = bytes.fromhex(ev.hex)
                    sub = scan_combined_frames(data, stop_after=2)
                    if sub > 1:
                        suspects.append(
                            f"{ev.ts} {client} RSP contains {sub} valid subframes (possible mis-framing)"
                        )
                except Exception:
                    pass
        # Event accounting
        if ev.event == "downstream_timeout":
            cs.timeouts += 1
            cs.timeout_streak += 1
            suspects.append(f"{ev.ts} {client} TIMEOUT ({ev.raw.get('timeout')})")
        else:
            cs.timeout_streak = 0
        if ev.role == "DROP":
            cs.drops += 1
            suspects.append(
                f"{ev.ts} {client} DROP reason={ev.raw.get('reason')} len={ev.total_len}"
            )
        if ev.event in {"shine_serial_error", "shine_open_failed"}:
            cs.serial_errors += 1
            suspects.append(f"{ev.ts} {client} {ev.event} error={ev.raw.get('error')}")

    # Output summary
    print("=== Summary by client ===")
    for client, cs in sorted(stats.items()):
        print(
            f"{client:>20}  REQ={cs.req:6d} RSP={cs.rsp:6d} timeouts={cs.timeouts:5d} "
            f"drops={cs.drops:5d} crc_bad={cs.crc_bad:4d} unk_func={cs.unknown_func:3d} large={cs.large_frames:3d}"
        )
    print()

    # Top suspects
    print("=== Suspect events ===")
    for line in suspects[:500]:
        print(line)


def _parse_optional_ts(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    return parse_ts(s)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Analyze Growatt broker sniff logs for failures and mis-framing"
    )
    ap.add_argument("paths", nargs="*", help="JSONL log files or '-' for stdin")
    ap.add_argument(
        "--client",
        dest="client",
        help="Filter by client label (e.g., SHINE or TCP:x:y)",
    )
    ap.add_argument(
        "--no-combined",
        action="store_true",
        help="Disable combined-frame detection scan",
    )
    ap.add_argument(
        "--combined-threshold",
        type=int,
        default=64,
        help="Minimum total bytes before running combined-frame scan (default: 64)",
    )
    ap.add_argument(
        "--since", help="Only analyze events at or after this ISO timestamp (UTC)"
    )
    ap.add_argument(
        "--until", help="Only analyze events before this ISO timestamp (UTC)"
    )
    ap.add_argument(
        "--limit-lines",
        type=int,
        help="Stop after reading N matching lines (for quick scans)",
    )
    args = ap.parse_args()

    paths = args.paths if args.paths else ["-"]
    analyze(
        paths,
        client_filter=args.client,
        detect_combined=not args.no_combined,
        combined_threshold=args.combined_threshold,
        since=_parse_optional_ts(args.since),
        until=_parse_optional_ts(args.until),
        limit_lines=args.limit_lines,
    )


if __name__ == "__main__":
    main()
