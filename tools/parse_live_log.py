#!/usr/bin/env python3
"""Phase 1 parser for broker JSONL sniff logs.

Single-file, compact implementation with two modes:
- --quick: fast bounded scan for baseline metrics
- full summarise: filtered response windows and register stats
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable


def modbus_crc(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if (crc & 1) else (crc >> 1)
    return crc & 0xFFFF


def crc_ok(frame: bytes) -> bool:
    if len(frame) < 4:
        return False
    return modbus_crc(frame[:-2]) == int.from_bytes(frame[-2:], "little")


def scan_combined(hexs: str, max_frame: int = 512) -> int:
    try:
        data = bytes.fromhex(hexs)
    except Exception:
        return 0
    count = 0
    i = 0
    L = len(data)
    while i + 4 <= L:
        found = False
        max_j = min(L, i + max_frame)
        for j in range(i + 4, max_j + 1):
            if crc_ok(data[i:j]):
                count += 1
                i = j
                found = True
                break
        if not found:
            i += 1
    return count


@dataclass
class RegisterStats:
    count: int = 0
    min_value: int | None = None
    max_value: int | None = None
    unique_values: set[int] = None

    def __post_init__(self) -> None:
        if self.unique_values is None:
            self.unique_values = set()

    def record(self, value: int) -> None:
        self.count += 1
        if self.min_value is None or value < self.min_value:
            self.min_value = value
        if self.max_value is None or value > self.max_value:
            self.max_value = value
        self.unique_values.add(value)


def decode_registers_from_pdu_hex(pdu_hex: str) -> list[int]:
    try:
        raw = bytes.fromhex(pdu_hex)
    except Exception:
        return []
    if len(raw) < 3:
        return []
    bytecount = raw[1]
    data = raw[2 : 2 + bytecount]
    if len(data) % 2:
        return []
    regs: list[int] = []
    for i in range(0, len(data), 2):
        regs.append((data[i] << 8) | data[i + 1])
    return regs


def iter_records(path: Path) -> Iterable[dict]:
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line_no, line in enumerate(fh, start=1):
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except json.JSONDecodeError:
                continue
            obj["_line"] = line_no
            yield obj


def quick_summary(path: Path, lines: int = 20000) -> None:
    by_event: Dict[str, int] = defaultdict(int)
    by_client: Dict[str, int] = defaultdict(int)
    crc_fail = 0
    timeouts = 0
    drops = 0
    combined_suspects = 0
    first_ts = None
    last_ts = None
    total = 0

    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for ln, line in enumerate(fh):
            if ln >= lines:
                break
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except Exception:
                continue
            total += 1
            ts = obj.get("ts")
            if ts:
                try:
                    t = datetime.fromisoformat(ts)
                    if first_ts is None:
                        first_ts = t
                    last_ts = t
                except Exception:
                    pass
            ev = obj.get("event") or obj.get("role") or "UNKNOWN"
            by_event[ev] += 1
            client = (
                obj.get("from_client")
                or obj.get("to_client")
                or obj.get("from")
                or "GLOBAL"
            )
            by_client[client] += 1
            if obj.get("crc_ok") is False:
                crc_fail += 1
            if obj.get("event") == "downstream_timeout":
                timeouts += 1
            if obj.get("role") == "DROP" or obj.get("reason") == "bad_crc":
                drops += 1
            hexs = obj.get("hex") or ""
            if hexs:
                try:
                    if scan_combined(hexs) > 1:
                        combined_suspects += 1
                except Exception:
                    pass

    print(f"Scanned: {total} lines (max {lines})")
    if first_ts and last_ts:
        print(f"Time range: {first_ts.isoformat()} -> {last_ts.isoformat()}")
    print("Top events:")
    for k, v in sorted(by_event.items(), key=lambda x: -x[1])[:20]:
        print(f"  {k}: {v}")
    print("Top clients:")
    for k, v in sorted(by_client.items(), key=lambda x: -x[1])[:10]:
        print(f"  {k}: {v}")
    print(
        f"crc_fail: {crc_fail}  timeouts: {timeouts}  drops: {drops}  combined_suspects: {combined_suspects}"
    )


def summarise(
    logfile: Path,
    *,
    to_filters: set[str] | None,
    from_filters: set[str] | None,
    func_filters: set[int] | None,
    since: datetime | None,
    until: datetime | None,
    register_history: set[int],
    limit: int | None,
    top: int,
) -> None:
    response_summary: Counter = Counter()
    register_stats: dict[int, RegisterStats] = defaultdict(RegisterStats)
    register_histories: dict[int, list[tuple[str | None, int, str | None]]] = {
        reg: [] for reg in register_history
    }

    wants_req_link = bool(register_history)
    req_buffer: dict[int, list[dict]] = defaultdict(list) if wants_req_link else {}

    frames_processed = 0
    for record in iter_records(logfile):
        role = record.get("role")
        func = record.get("func")
        ts = None
        if record.get("ts"):
            try:
                ts = datetime.fromisoformat(record.get("ts"))
            except Exception:
                ts = None
        if since and ts and ts < since:
            continue
        if until and ts and ts > until:
            continue

        if role == "REQ":
            endpoint = record.get("from_client")
            if from_filters and endpoint not in from_filters:
                continue
            if func_filters and func not in func_filters:
                continue
            if wants_req_link and func is not None:
                req_buffer[func].append(record)
            continue

        if role != "RSP":
            continue

        endpoint = record.get("to_client")
        if to_filters and endpoint not in to_filters:
            continue
        if func_filters and func not in func_filters:
            continue

        frames_processed += 1
        addr = record.get("addr")
        count = record.get("count")
        response_summary[(endpoint or "?", func, addr, count)] += 1

        if func in {3, 4} and addr is not None:
            regs = decode_registers_from_pdu_hex(record.get("hex", ""))
            if wants_req_link:
                reqs = req_buffer.get(func) or []
                related_req = reqs.pop(0) if reqs else None
                req_addr = related_req.get("addr") if related_req else None
            else:
                req_addr = None

            for offset, value in enumerate(regs):
                reg_index = addr + offset
                register_stats[reg_index].record(value)
                if reg_index in register_histories:
                    register_histories[reg_index].append(
                        (record.get("ts"), value, endpoint)
                    )

        if limit and frames_processed >= limit:
            break

    print(f"Frames processed: {frames_processed}")
    if not frames_processed:
        return

    print("\nResponse windows (top):")
    header = f"{'target':<28}{'func':>6}{'addr':>10}{'count':>8}{'frames':>10}"
    print(header)
    print("-" * len(header))
    for target, func, addr, count in sorted(
        response_summary,
        key=lambda key: (-response_summary[key], key[0], key[1] or -1, key[2] or -1),
    )[:top]:
        freq = response_summary[(target, func, addr, count)]
        addr_disp = str(addr) if addr is not None else "-"
        count_disp = str(count) if count is not None else "-"
        func_disp = str(func) if func is not None else "-"
        print(f"{target:<28}{func_disp:>6}{addr_disp:>10}{count_disp:>8}{freq:>10}")

    if register_stats:
        print("\nRegister coverage (top observed registers):")
        for reg in sorted(register_stats, key=lambda r: -register_stats[r].count)[:top]:
            s = register_stats[reg]
            print(
                f"  reg {reg}: obs={s.count} min={s.min_value} max={s.max_value} unique={len(s.unique_values)}"
            )

    for reg, hist in register_histories.items():
        print(f"\nHistory for register {reg} ({len(hist)} samples):")
        for ts, v, endpoint in hist:
            print(f"  {ts or '-'}  {endpoint or '-':<28}  {v}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", nargs="?", default="broker-210925.log")
    ap.add_argument("--quick", action="store_true", help="Quick summary mode")
    ap.add_argument("--lines", type=int, default=20000, help="Max lines for quick mode")
    ap.add_argument("--to", nargs="*", help="Only include responses to these endpoints")
    ap.add_argument(
        "--from",
        dest="from_clients",
        nargs="*",
        help="Only include requests from these endpoints",
    )
    ap.add_argument("--func", nargs="*", type=int, help="Restrict to function codes")
    ap.add_argument("--since", type=str, help="ISO start time filter")
    ap.add_argument("--until", type=str, help="ISO end time filter")
    ap.add_argument("--limit", type=int, help="Stop after this many response frames")
    ap.add_argument("--top", type=int, default=10, help="Rows to show in summaries")
    ap.add_argument(
        "--dump-register",
        dest="dump_registers",
        nargs="*",
        type=int,
        help="Records to dump",
    )

    args = ap.parse_args()
    path = Path(args.path)
    if not path.exists():
        raise SystemExit(f"Log not found: {path}")

    if args.quick:
        quick_summary(path, lines=args.lines)
        return

    to_filters = set(args.to) if args.to else None
    from_filters = set(args.from_clients) if args.from_clients else None
    func_filters = set(args.func) if args.func else None
    since = datetime.fromisoformat(args.since) if args.since else None
    until = datetime.fromisoformat(args.until) if args.until else None
    register_history = set(args.dump_registers or [])

    summarise(
        path,
        to_filters=to_filters,
        from_filters=from_filters,
        func_filters=func_filters,
        since=since,
        until=until,
        register_history=register_history,
        limit=args.limit,
        top=args.top,
    )


if __name__ == "__main__":
    main()
