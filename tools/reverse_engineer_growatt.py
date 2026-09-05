#!/usr/bin/env python3
"""Helper utilities to reverse engineer Growatt broker frames.

Given one or more JSONL sniff logs (as produced by the broker's wire logger),
this script groups frames by function code and role, then surfaces statistics
that highlight which byte offsets vary, how many unique payloads exist, and
provides sample frames for manual inspection.

Example usage:
  python tools/reverse_engineer_growatt.py broker-260925-2.log \
      --func 32 --role RSP --word-sizes 1 2 --top-payloads 5
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional

from analyze_sniff_log import Event, is_tcp_client, parse_ts, read_events


def _parse_optional_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return parse_ts(value)


def _parse_int(text: str) -> int:
    return int(text, 0)


def _fmt_ts(ts: Optional[datetime]) -> str:
    return ts.isoformat() if ts else "-"


def _top_values(counter: Counter[int], limit: int = 5) -> str:
    parts: List[str] = []
    for value, count in counter.most_common(limit):
        parts.append(f"0x{value:02X}({count})")
    if len(counter) > limit:
        parts.append("…")
    return ", ".join(parts)


def _byte_ranges(counters: List[Counter[int]], variable: bool) -> List[str]:
    ranges: List[str] = []
    start: Optional[int] = None
    for idx, counter in enumerate(counters):
        is_variable = len(counter) > 1
        if is_variable == variable:
            if start is None:
                start = idx
        elif start is not None:
            end = idx - 1
            ranges.append(f"{start}-{end}" if end > start else f"{start}")
            start = None
    if start is not None:
        end = len(counters) - 1
        ranges.append(f"{start}-{end}" if end > start else f"{start}")
    return ranges


@dataclass
class LengthStats:
    length: int
    word_sizes: List[int]
    byte_order: str
    sample_limit: int
    count: int = 0
    first_ts: Optional[datetime] = None
    last_ts: Optional[datetime] = None
    unique_payloads: set[str] = field(default_factory=set)
    examples: List[tuple[datetime, str]] = field(default_factory=list)
    byte_counters: List[Counter[int]] = field(init=False)
    word_counters: Dict[int, List[Counter[int]]] = field(init=False)

    def __post_init__(self) -> None:
        self.byte_counters = [Counter() for _ in range(self.length)]
        self.word_counters = {
            size: [Counter() for _ in range(self.length // size)]
            for size in self.word_sizes
            if size > 0
        }

    def add(self, payload: str, ts: Optional[datetime]) -> None:
        try:
            data = bytes.fromhex(payload)
        except ValueError:
            return
        if len(data) != self.length:
            return

        self.count += 1
        if ts:
            if self.first_ts is None or ts < self.first_ts:
                self.first_ts = ts
            if self.last_ts is None or ts > self.last_ts:
                self.last_ts = ts

        self.unique_payloads.add(payload)
        if len(self.examples) < self.sample_limit and ts:
            self.examples.append((ts, payload))

        for idx, value in enumerate(data):
            self.byte_counters[idx][value] += 1

        for size, counters in self.word_counters.items():
            if size <= 0 or len(data) % size:
                continue
            for word_index in range(0, len(data), size):
                chunk = data[word_index : word_index + size]
                if self.byte_order == "little":
                    word_value = int.from_bytes(chunk, "little")
                else:
                    word_value = int.from_bytes(chunk, "big")
                counters[word_index // size][word_value] += 1

    def describe_bytes(self, show_constant: bool, top: int) -> Iterable[str]:
        for idx, counter in enumerate(self.byte_counters):
            if not counter:
                continue
            unique = len(counter)
            if unique == 1 and not show_constant:
                continue
            total = sum(counter.values())
            values = sorted(counter.keys())
            min_val = values[0]
            max_val = values[-1]
            yield (
                f"    [b{idx:03d}] unique={unique:3d} total={total:5d} "
                f"min=0x{min_val:02X} max=0x{max_val:02X} top={_top_values(counter, top)}"
            )

    def describe_words(self, show_constant: bool, top: int) -> Iterable[str]:
        for size, counters in sorted(self.word_counters.items()):
            if size <= 1:
                # size 1 duplicates the byte view
                continue
            if not counters:
                continue
            emitted_header = False
            for idx, counter in enumerate(counters):
                if not counter:
                    continue
                unique = len(counter)
                if unique == 1 and not show_constant:
                    continue
                if not emitted_header:
                    yield f"    Word size {size} ({self.byte_order}-endian)"
                    emitted_header = True
                total = sum(counter.values())
                values = sorted(counter.keys())
                min_val = values[0]
                max_val = values[-1]
                yield (
                    f"      [w{idx:03d}] unique={unique:3d} total={total:5d} "
                    f"min=0x{min_val:0{size*2}X} max=0x{max_val:0{size*2}X} "
                    f"top={_top_values(counter, top)}"
                )



@dataclass
class FuncRoleStats:
    func: Optional[int]
    role: Optional[str]
    word_sizes: List[int]
    byte_order: str
    sample_limit: int
    count: int = 0
    first_ts: Optional[datetime] = None
    last_ts: Optional[datetime] = None
    clients: set[str] = field(default_factory=set)
    lengths: Dict[int, LengthStats] = field(default_factory=dict)
    payload_counter: Counter[str] = field(default_factory=Counter)

    def add(self, ev: Event, client: str) -> None:
        payload = ev.hex or ""
        if not payload:
            return
        length = len(payload) // 2
        if length <= 0:
            return

        ts = ev.ts
        self.count += 1
        if ts:
            if self.first_ts is None or ts < self.first_ts:
                self.first_ts = ts
            if self.last_ts is None or ts > self.last_ts:
                self.last_ts = ts
        self.clients.add(client)
        self.payload_counter[payload] += 1

        stats = self.lengths.get(length)
        if stats is None:
            stats = LengthStats(
                length=length,
                word_sizes=self.word_sizes,
                byte_order=self.byte_order,
                sample_limit=self.sample_limit,
            )
            self.lengths[length] = stats
        stats.add(payload, ts)


def collect_stats(
    paths: List[str],
    *,
    client_filter: Optional[str],
    include_tcp: bool,
    func_filter: Optional[set[int]],
    role_filter: Optional[str],
    since: Optional[datetime],
    until: Optional[datetime],
    limit: Optional[int],
    word_sizes: List[int],
    byte_order: str,
    sample_limit: int,
) -> Dict[tuple[Optional[int], Optional[str]], FuncRoleStats]:
    stats: Dict[tuple[Optional[int], Optional[str]], FuncRoleStats] = {}

    total_processed = 0
    for ev in read_events(paths, since=since, until=until, limit_lines=limit):
        client = ev.client_from or ev.client_to or "(unknown)"

        if client_filter and client_filter not in client:
            continue

        is_tcp = is_tcp_client(ev.client_from) or is_tcp_client(ev.client_to)
        if is_tcp and not include_tcp:
            continue

        if role_filter and ev.role != role_filter:
            continue

        if func_filter is not None:
            if ev.func is None or ev.func not in func_filter:
                continue

        key = (ev.func, ev.role)
        if key not in stats:
            stats[key] = FuncRoleStats(
                func=ev.func,
                role=ev.role,
                word_sizes=word_sizes,
                byte_order=byte_order,
                sample_limit=sample_limit,
            )
        stats[key].add(ev, client)

        total_processed += 1
        if limit and total_processed >= limit:
            break

    return stats


def emit_report(
    stats: Dict[tuple[Optional[int], Optional[str]], FuncRoleStats],
    *,
    top_payloads: int,
    top_values: int,
    show_constant: bool,
) -> None:
    if not stats:
        print("No matching frames found.")
        return

    for key in sorted(stats.keys()):
        func, role = key
        entry = stats[key]
        func_label = "?" if func is None else f"0x{func:02X}" if func >= 0 else str(func)
        print("=== Function", func_label, f"role={role or '-'} ===")
        print(
            f"Total frames: {entry.count}  distinct payloads: {len(entry.payload_counter)}  "
            f"clients: {len(entry.clients)}  first={_fmt_ts(entry.first_ts)}  last={_fmt_ts(entry.last_ts)}"
        )
        if entry.clients:
            sample_clients = ", ".join(sorted(entry.clients))
            print(f"Clients observed: {sample_clients}")

        print("Top payloads:")
        for payload, count in entry.payload_counter.most_common(top_payloads):
            print(f"  count={count:5d} len={len(payload)//2:3d} hex={payload}")

        for length in sorted(entry.lengths.keys()):
            length_stats = entry.lengths[length]
            print(
                f"  Length {length} bytes: count={length_stats.count} "
                f"unique={len(length_stats.unique_payloads)} "
                f"first={_fmt_ts(length_stats.first_ts)} last={_fmt_ts(length_stats.last_ts)}"
            )
            variable_ranges = _byte_ranges(length_stats.byte_counters, variable=True)
            constant_ranges = _byte_ranges(length_stats.byte_counters, variable=False)
            if variable_ranges:
                print(f"    Variable byte ranges: {', '.join(variable_ranges)}")
            if show_constant and constant_ranges:
                print(f"    Constant byte ranges: {', '.join(constant_ranges)}")

            print("    Byte detail:")
            emitted = False
            for line in length_stats.describe_bytes(show_constant=show_constant, top=top_values):
                print(line)
                emitted = True
            if not emitted:
                print("      (all bytes constant; use --show-constant to list)")

            for line in length_stats.describe_words(show_constant=show_constant, top=top_values):
                print(line)

            if length_stats.examples:
                print("    Sample payloads:")
                for ts, payload in length_stats.examples:
                    print(f"      {ts.isoformat()}  {payload}")

        print()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarise Growatt broker frames to assist reverse engineering",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="JSONL log files or '-' for stdin",
    )
    parser.add_argument(
        "--client",
        help="Match frames where the client label contains this substring",
    )
    parser.add_argument(
        "--include-tcp",
        action="store_true",
        help="Include TCP:* clients even when --client is unset",
    )
    parser.add_argument(
        "--func",
        action="append",
        type=_parse_int,
        help="Filter by Modbus function code (decimal or 0x-prefixed). Repeatable.",
    )
    parser.add_argument(
        "--role",
        choices=["REQ", "RSP", "DROP"],
        help="Filter on frame role",
    )
    parser.add_argument(
        "--since",
        help="Only include events at or after this ISO timestamp (UTC)",
    )
    parser.add_argument(
        "--until",
        help="Only include events before this ISO timestamp (UTC)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Stop after consuming N matching frames",
    )
    parser.add_argument(
        "--word-sizes",
        type=int,
        nargs="+",
        default=[1, 2],
        help="Word sizes (in bytes) for aggregate stats. Default: 1 2",
    )
    parser.add_argument(
        "--byte-order",
        choices=["big", "little"],
        default="big",
        help="Byte order to use when evaluating word-sized aggregates",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=5,
        help="Store up to this many sample payloads per length",
    )
    parser.add_argument(
        "--top-payloads",
        type=int,
        default=10,
        help="Show the top-N payloads by frequency (default: 10)",
    )
    parser.add_argument(
        "--top-values",
        type=int,
        default=5,
        help="Show the top-N byte/word values at each offset (default: 5)",
    )
    parser.add_argument(
        "--show-constant",
        action="store_true",
        help="Include byte/word offsets that never change",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    paths = args.paths if args.paths else ["-"]
    func_filter = set(args.func) if args.func else None
    since = _parse_optional_ts(args.since)
    until = _parse_optional_ts(args.until)

    stats = collect_stats(
        paths,
        client_filter=args.client,
        include_tcp=args.include_tcp,
        func_filter=func_filter,
        role_filter=args.role,
        since=since,
        until=until,
        limit=args.limit,
        word_sizes=args.word_sizes,
        byte_order=args.byte_order,
        sample_limit=args.sample_limit,
    )

    emit_report(
        stats,
        top_payloads=args.top_payloads,
        top_values=args.top_values,
        show_constant=args.show_constant,
    )


if __name__ == "__main__":
    main()
