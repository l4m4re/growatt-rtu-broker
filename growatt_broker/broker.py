#!/usr/bin/env python3
"""
Growatt RTU Broker
 - Serial RS-485 (downstream) single master to inverter
 - Upstream endpoints:
    * ShineWiFi-X serial passthrough
    * Modbus-TCP server for HA/tools
 - Enforces min request spacing, logs wire traffic
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import signal
import socket
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import serial

from .cache_gateway import (
    CachePolicy,
    CachedRead,
    GatewayResult,
    PatternKey,
    PollCoordinator,
    RegisterCache,
    RegisterKey,
    RegisterSnapshot,
    ShinePatternObserver,
    ShineVirtualInverterAdapter,
    build_read_response,
    min_6000tl_xh_discovery_profile,
)
from .cache_gateway import crc_ok as cache_crc_ok
from .configuration import (
    ConfigurationError,
    InstallationConfig,
    SetupPlanObserver,
    load_installation_config,
)


def modbus_crc(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if (crc & 1) else (crc >> 1)
    return crc & 0xFFFF


def add_crc(body: bytes) -> bytes:
    c = modbus_crc(body)
    return body + c.to_bytes(2, "little")


def crc_ok(frame: bytes) -> bool:
    if len(frame) < 4:
        return False
    return modbus_crc(frame[:-2]) == int.from_bytes(frame[-2:], "little")


def standard_response_spec(request: bytes) -> tuple[int, int, int] | None:
    """Return unit, function, and normal response length for standard requests."""
    if len(request) < 8 or not crc_ok(request):
        return None

    unit, function = request[:2]
    if function in (0x03, 0x04, 0x20):
        if len(request) != 8:
            return None
        count = int.from_bytes(request[4:6], "big")
        return unit, function, 5 + (count * 2)
    if function in (0x06, 0x10):
        return unit, function, 8
    return None


def find_standard_response(
    buffer: bytes, request: bytes, *, allow_unit_zero_wildcard: bool = False
) -> bytes | None:
    """Find one complete response matching a standard Modbus request.

    Response length is derived from the request.  This deliberately does not
    accept an arbitrary CRC-valid substring, because a partial FC03/FC04 frame
    can itself contain a valid CRC.
    """
    spec = standard_response_spec(request)
    if spec is None:
        return None

    unit, function, normal_length = spec
    for start in range(len(buffer)):
        if buffer[start] != unit and not (
            allow_unit_zero_wildcard and unit == 0 and buffer[start] != 0
        ):
            continue
        if start + 5 <= len(buffer):
            candidate = buffer[start : start + 5]
            if candidate[1] == (function | 0x80) and crc_ok(candidate):
                return candidate
        if start + normal_length > len(buffer):
            continue
        candidate = buffer[start : start + normal_length]
        if candidate[1] != function or not crc_ok(candidate):
            continue
        if function in (0x03, 0x04, 0x20) and candidate[2] != (normal_length - 5):
            continue
        if function == 0x06 and candidate != request:
            continue
        if function == 0x10:
            expected = add_crc(request[0:6])
            if candidate != expected:
                continue
        return candidate
    return None


class RTUFramer:
    def __init__(
        self,
        ser: serial.Serial,
        char_time: float,
        gap_chars: float = 3.5,
        capture: Callable[[bytes, str], None] | None = None,
        on_unframed: Callable[[bytes, str], None] | None = None,
        resync: bool = True,
    ):
        self.ser = ser
        self.char_time = char_time
        self.capture = capture
        # At high baud on Linux, user-space gaps between recv bursts can be > a few ms.
        # Use 3.5 char times but never below a safe floor to avoid premature frame cuts.
        gap_floor = 0.002  # 2 ms floor
        self.gap = max(gap_chars * char_time, gap_floor)
        self.buf = bytearray()
        self.last = time.perf_counter()
        self.on_unframed = on_unframed
        self.resync = resync

    def _first_crc_frame(
        self,
        data: bytes,
    ) -> tuple[int, int, bytes] | None:
        starts = range(len(data) - 3) if self.resync else (0,)
        for frame_start in starts:
            for frame_end in range(frame_start + 4, len(data) + 1):
                candidate = data[frame_start:frame_end]
                if crc_ok(candidate):
                    return frame_start, frame_end, candidate
        return None

    def _report_unframed(self, data: bytes, reason: str) -> None:
        if data and self.on_unframed is not None:
            self.on_unframed(data, reason)

    def _read_available(self, source: str) -> None:
        count = self.ser.in_waiting
        if not count:
            return
        data = self.ser.read(count)
        if data:
            self.buf.extend(data)
            self.last = time.perf_counter()
            if self.capture:
                self.capture(data, source)

    def drain_complete_frames(self, on_frame: Callable[[bytes], None]) -> None:
        """Remove and report CRC-valid frames already waiting in the buffer."""
        while len(self.buf) >= 4:
            data = bytes(self.buf)
            found = self._first_crc_frame(data)
            if found is None:
                return
            frame_start, frame_end, candidate = found
            del self.buf[:frame_end]
            on_frame(candidate)

    def read_frame(self, timeout: float = 3.0) -> bytes:
        start = time.perf_counter()
        while True:
            now = time.perf_counter()
            if now - start > timeout:
                residual = bytes(self.buf)
                if len(self.buf) >= 4:
                    buf = residual
                    found = self._first_crc_frame(buf)
                    if found is not None:
                        start_idx, end_idx, frame = found
                        self._report_unframed(
                            buf[:start_idx], "prefix_before_crc_frame"
                        )
                        remaining = buf[end_idx:]
                        self.buf.clear()
                        if remaining:
                            self.buf.extend(remaining)
                        return frame
                self._report_unframed(residual, "unframed_timeout")
                if len(residual) > 8192 and self.capture:
                    self.capture(residual, "buffer_overflow_discard")
                self.buf.clear()
                return b""

            n = self.ser.in_waiting
            if n:
                self._read_available("normal_read")
                now = time.perf_counter()
            else:
                if self.buf and (now - self.last) >= self.gap:
                    # Attempt to find a CRC-terminated frame inside the buffer.
                    # This handles combined frames (frame1+frame2) by returning
                    # the first valid frame and leaving the remainder in the buffer.
                    if len(self.buf) >= 4:
                        # Search for a valid frame anywhere in the buffer (handles
                        # possible mis-alignment if we started reading mid-frame).
                        buf = bytes(self.buf)
                        found = self._first_crc_frame(buf)
                        if found is not None:
                            start_idx, end_idx, frame = found
                            self._report_unframed(
                                buf[:start_idx], "prefix_before_crc_frame"
                            )
                            remaining = buf[end_idx:]
                            self.buf.clear()
                            if remaining:
                                self.buf.extend(remaining)
                            return frame
                        # No valid CRC-terminated frame found; fallthrough to
                        # timeout handling below (do not return partial data yet)
                    else:
                        # Buffer too small to contain a full frame, fallthrough
                        pass
                if (now - start) > timeout:
                    # On timeout: if we have a CRC-terminated frame in the buffer,
                    # return it. Otherwise, do not return a partial frame (return
                    # empty to indicate timeout) — this prevents higher layers from
                    # processing incomplete frames which would fail CRC checks.
                    if len(self.buf) >= 4:
                        buf = bytes(self.buf)
                        found = self._first_crc_frame(buf)
                        if found is not None:
                            start_idx, end_idx, frame = found
                            self._report_unframed(
                                buf[:start_idx], "prefix_before_crc_frame"
                            )
                            remaining = buf[end_idx:]
                            self.buf.clear()
                            if remaining:
                                self.buf.extend(remaining)
                            return frame
                    # Protect against runaway buffer growth: if buffer gets very
                    # large and no valid frame is detected, drop it and return
                    # timeout to avoid memory issues.
                    if len(self.buf) > 8192:
                        self._report_unframed(
                            bytes(self.buf), "buffer_overflow_discard"
                        )
                        if self.capture:
                            self.capture(bytes(self.buf), "buffer_overflow_discard")
                        self.buf.clear()
                    return b""
                # Don’t sleep sub-millisecond; use a fixed sleep to reduce CPU use.
                time.sleep(max(0.001, self.char_time * 0.5))

    def read_standard_frame(
        self,
        request: bytes,
        timeout: float = 3.0,
        on_unmatched: Callable[[bytes], None] | None = None,
        allow_unit_zero_wildcard: bool = False,
    ) -> bytes:
        """Read an exact-length response for a standard Modbus request."""
        if standard_response_spec(request) is None:
            return self.read_frame(timeout=timeout)

        unit, function, normal_length = standard_response_spec(request)
        start = time.perf_counter()

        def first_valid_frame(
            data: bytes, limit: int | None = None
        ) -> tuple[int, int, bytes] | None:
            end_limit = len(data) if limit is None else min(limit, len(data))
            for frame_start in range(max(0, end_limit - 3)):
                if frame_start + 1 >= end_limit:
                    break
                if data[frame_start] not in (0, unit) and not (
                    allow_unit_zero_wildcard and unit == 0
                ):
                    continue
                function = data[frame_start + 1]
                lengths: list[int] = []
                if function & 0x80:
                    lengths.append(5)
                elif function in (0x03, 0x04, 0x20):
                    lengths.append(8)
                    if frame_start + 2 < end_limit:
                        response_length = 5 + data[frame_start + 2]
                        if response_length != 8:
                            lengths.append(response_length)
                elif function in (0x06,):
                    lengths.append(8)
                elif function == 0x10:
                    lengths.append(8)
                    if frame_start + 6 < end_limit:
                        lengths.append(9 + data[frame_start + 6])
                else:
                    if frame_start > 64:
                        continue
                    lengths.extend(
                        range(4, min(32, end_limit - frame_start) + 1)
                    )
                for frame_length in lengths:
                    frame_end = frame_start + frame_length
                    if frame_end <= end_limit:
                        candidate = data[frame_start:frame_end]
                        if crc_ok(candidate):
                            return frame_start, frame_end, candidate
            return None

        def possible_partial_response(candidate: bytes) -> bool:
            return (
                len(candidate) < normal_length
                and len(candidate) >= 3
                and (
                    candidate[0] == unit
                    or (allow_unit_zero_wildcard and unit == 0 and candidate[0] != 0)
                )
                and candidate[1] == function
                and candidate[2] == normal_length - 5
            )

        def report_unmatched_prefix(limit: int) -> None:
            while on_unmatched is not None:
                found = first_valid_frame(bytes(self.buf), limit)
                if found is None:
                    return
                frame_start, frame_end, candidate = found
                if frame_start == 0 and possible_partial_response(candidate):
                    return
                del self.buf[:frame_end]
                limit -= frame_end
                on_unmatched(candidate)

        deadline = start + timeout
        while True:
            if time.perf_counter() >= deadline:
                return b""
            if self.ser.in_waiting:
                self._read_available("normal_read")
                if time.perf_counter() >= deadline:
                    return b""
            buffer = bytes(self.buf)
            response = find_standard_response(
                buffer,
                request,
                allow_unit_zero_wildcard=allow_unit_zero_wildcard,
            )
            if response is not None:
                response_start = buffer.find(response)
                report_unmatched_prefix(response_start)
                if bytes(self.buf).find(response) == -1:
                    continue
                end = self.buf.find(response) + len(response)
                del self.buf[:end]
                return response

            if on_unmatched is not None and time.perf_counter() - self.last >= self.gap:
                report_unmatched_prefix(len(self.buf))

            time.sleep(max(0.001, self.char_time * 0.5))

    def read_matching(
        self,
        matcher: Callable[[bytes], bool],
        *,
        timeout: float = 3.0,
        on_unmatched: Callable[[bytes], None] | None = None,
    ) -> bytes:
        """Read frames until one satisfies the caller's response predicate."""
        deadline = time.perf_counter() + timeout
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return b""
            frame = self.read_frame(timeout=remaining)
            if not frame:
                return b""
            if matcher(frame):
                return frame
            if on_unmatched is not None:
                on_unmatched(frame)



def now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="milliseconds")


def parse_host_port(spec: str) -> tuple[str, int]:
    if ":" not in spec:
        raise ValueError(f"invalid address '{spec}' (expected host:port)")
    host, port_s = spec.rsplit(":", 1)
    host = host or "0.0.0.0"
    try:
        port = int(port_s)
    except ValueError as exc:
        raise ValueError(f"invalid port in '{spec}'") from exc
    return host, port


def parse_rtu(frame: bytes) -> dict:
    if len(frame) < 4:
        return {}
    uid, func = frame[0], frame[1]
    body = frame[2:-2]
    info = {"uid": uid, "func": func, "len": len(body)}
    if func in (0x03, 0x04) and len(body) >= 4:
        info["addr"] = (body[0] << 8) | body[1]
        info["count"] = (body[2] << 8) | body[3]
    elif func == 0x06 and len(body) >= 4:
        info["addr"] = (body[0] << 8) | body[1]
        info["value"] = (body[2] << 8) | body[3]
    elif func == 0x10 and len(body) >= 5:
        info["addr"] = (body[0] << 8) | body[1]
        info["count"] = (body[2] << 8) | body[3]
        info["bytes"] = body[4]
    return info


class EventSink:
    def handle(self, event: dict) -> None:
        raise NotImplementedError


class EventHub:
    def __init__(self, sinks: Iterable[EventSink] | None = None):
        self.sinks: List[EventSink] = list(sinks or [])

    def emit(self, **event) -> None:
        if not self.sinks:
            return
        payload = dict(event)
        payload.setdefault("ts", now_iso())
        for sink in list(self.sinks):
            try:
                sink.handle(dict(payload))
            except Exception:
                # Individual sink failures must not affect the broker loop
                pass


class WireLogger(EventSink):
    def __init__(self, path: str | None):
        self.path = path
        self._lock = threading.Lock()
        # Determine logging mode: 'file', 'console', or 'disabled'
        if path is None or path == "" or path == "-":
            self._mode = "console"
        elif isinstance(path, str) and path.lower() == "none":
            self._mode = "disabled"
        else:
            self._mode = "file"
            try:
                d = os.path.dirname(self.path) or "."
                os.makedirs(d, exist_ok=True)
                with open(self.path, "a", encoding="utf-8"):
                    pass
            except Exception:
                # Fall back to console if file cannot be prepared
                self._mode = "console"

    def enabled(self) -> bool:
        return self._mode != "disabled"

    def handle(self, event: dict) -> None:
        if not self.enabled():
            return
        line = json.dumps(event, ensure_ascii=False)
        if self._mode == "console":
            with self._lock:
                try:
                    print(line, flush=True)
                except Exception:
                    pass
            return
        # file mode
        with self._lock:
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                # As a last resort, try console
                try:
                    print(line, flush=True)
                except Exception:
                    pass


class SnifferRelay(EventSink, threading.Thread):
    def __init__(self, host: str, port: int):
        threading.Thread.__init__(self, daemon=True)
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(self.addr)
        self.sock.listen(5)
        self._clients: List[socket.socket] = []
        self._lock = threading.Lock()

    def run(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                break
            conn.setblocking(True)
            with self._lock:
                self._clients.append(conn)

    def handle(self, event: dict) -> None:
        line = json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n"
        dead: List[socket.socket] = []
        with self._lock:
            clients = list(self._clients)
        for conn in clients:
            try:
                conn.sendall(line)
            except Exception:
                dead.append(conn)
        if dead:
            with self._lock:
                for conn in dead:
                    if conn in self._clients:
                        self._clients.remove(conn)
                    try:
                        conn.close()
                    except Exception:
                        pass


@dataclass
class DownstreamRequest:
    request: bytes
    client: str
    source: str
    standard_modbus: bool
    queued_ns: int
    done: threading.Event
    is_write: bool = False
    response: bytes = b""


class Downstream:
    """Single-owner downstream serial scheduler.

    The queue selects the next source at transaction boundaries; an active
    physical transaction is never preempted.
    """

    _WRITE_PRIORITIES = {
        "PROD_TCP": 0,
        "DEV_TCP": 1,
        "SHINE": 2,
    }
    _BACKGROUND_SOURCES = frozenset({"BACKGROUND"})

    def __init__(
        self,
        dev: str,
        baud: int,
        fmt: str,
        *,
        rtimeout: float = 0.85,
        reopen_after_timeouts: int = 10,
        events: Optional[EventHub] = None,
    ):
        self.dev = dev
        self.baud = baud
        self.fmt = fmt
        databits = int(fmt[0])
        parity = fmt[1].upper()
        stop = int(fmt[2])
        py_par = {
            "N": serial.PARITY_NONE,
            "E": serial.PARITY_EVEN,
            "O": serial.PARITY_ODD,
        }[parity]
        py_stp = {1: serial.STOPBITS_ONE, 2: serial.STOPBITS_TWO}[stop]
        self._serial_kwargs = {
            "bytesize": databits,
            "parity": py_par,
            "stopbits": py_stp,
            "timeout": 0,
        }
        self._serial_paths = self._discover_serial_paths(dev)
        self._active_serial_path = self._serial_paths[0]
        self.ser = self._open_serial()
        bits_per_char = 1 + databits + stop + (0 if parity == "N" else 1)
        self.char_time = bits_per_char / baud
        self.framer = RTUFramer(
            self.ser,
            self.char_time,
            on_unframed=self._report_unframed_inverter,
        )
        self._io_lock = threading.Lock()
        self._queue_condition = threading.Condition()
        self._pending: list[DownstreamRequest] = []
        self.rtimeout = float(rtimeout)
        self._consecutive_timeouts = 0
        self._reopen_after_timeouts = int(reopen_after_timeouts)
        self._async_frame_handler: Callable[[bytes], None] | None = None
        self.events = events
        self._scheduler = threading.Thread(
            target=self._run_scheduler, name="growatt-downstream", daemon=True
        )
        self._scheduler.start()

    @staticmethod
    def _discover_serial_paths(dev: str) -> tuple[str, ...]:
        """Keep a stable udev alias when the caller supplied a tty node."""
        if "/serial/by-" in dev:
            return (dev,)
        real_dev = os.path.realpath(dev)
        aliases: list[str] = []
        for directory in ("/dev/serial/by-id", "/dev/serial/by-path"):
            try:
                entries = sorted(Path(directory).iterdir())
            except OSError:
                continue
            aliases.extend(
                str(entry) for entry in entries if os.path.realpath(entry) == real_dev
            )
        return tuple(dict.fromkeys((*aliases, dev)))

    def _open_serial(self) -> serial.Serial:
        last_error: Exception | None = None
        for path in self._serial_paths:
            try:
                ser = serial.Serial(path, self.baud, **self._serial_kwargs)
            except (OSError, serial.SerialException) as exc:
                last_error = exc
                continue
            self._active_serial_path = path
            return ser
        if last_error is not None:
            raise last_error
        raise OSError(f"no serial path available for {self.dev}")

    def _reopen_serial(self, reason: str) -> bool:
        """Reopen the stable device path after a disconnect or bad run."""
        old = self.ser
        self.ser = None
        if old is not None:
            try:
                old.close()
            except (OSError, serial.SerialException):
                pass
        self._capture_event(
            "inverter_serial_reopen",
            role="WARN",
            reason=reason,
            port=self._active_serial_path,
            candidates=self._serial_paths,
        )
        for attempt in range(1, 4):
            try:
                self.ser = self._open_serial()
            except (OSError, serial.SerialException) as exc:
                self._capture_event(
                    "inverter_open_failed",
                    role="ERROR",
                    port=self._active_serial_path,
                    candidates=self._serial_paths,
                    attempt=attempt,
                    error=str(exc),
                )
                time.sleep(0.25 * attempt)
                continue
            self.framer.ser = self.ser
            self.framer.buf.clear()
            self.framer.last = time.perf_counter()
            try:
                self.ser.reset_input_buffer()
                self.ser.reset_output_buffer()
            except (OSError, serial.SerialException):
                self._capture_event(
                    "inverter_buffer_reset_failed",
                    role="WARN",
                    port=self._active_serial_path,
                )
            self._capture_event(
                "inverter_serial_reopened",
                role="INFO",
                port=self._active_serial_path,
                attempt=attempt,
            )
            return True
        return False

    def _capture_event(self, event: str, **fields: object) -> None:
        if self.events:
            self.events.emit(event=event, **fields)

    def set_async_frame_handler(self, handler: Callable[[bytes], None] | None) -> None:
        """Register the destination for valid unsolicited inverter frames."""
        self._async_frame_handler = handler

    def _report_unframed_inverter(self, data: bytes, reason: str) -> None:
        """Observe and preserve inverter bytes without valid RTU framing."""
        preview = data[:512]
        self._capture_event(
            "inverter_unframed_bytes",
            role="WIRE",
            source="INVERTER",
            direction="inverter_to_shine",
            reason=reason,
            length=len(data),
            preview_hex=preview.hex(),
            preview_truncated=len(preview) != len(data),
        )

    def _report_async_frame(self, request: bytes, frame: bytes) -> None:
        self._capture_event(
            "async_frame_observed",
            role="INFO",
            source="INVERTER",
            expected_function=request[1],
            hex=frame.hex(),
            **parse_rtu(frame),
        )
        if self._async_frame_handler is not None:
            try:
                self._async_frame_handler(frame)
            except Exception as exc:
                self._capture_event(
                    "async_frame_forward_failed",
                    role="WARN",
                    source="INVERTER",
                    error=str(exc),
                    hex=frame.hex(),
                )

    def _ensure_serial(self) -> bool:
        if self.ser is not None and self.ser.is_open:
            return True
        return self._reopen_serial("serial_not_open")

    @classmethod
    def _priority(cls, source: str, client: str, *, is_write: bool = False) -> int:
        if is_write:
            return cls._WRITE_PRIORITIES[source]
        if source in cls._BACKGROUND_SOURCES:
            return 3
        del client
        return 2

    def _select_request(self) -> DownstreamRequest:
        writes = [item for item in self._pending if item.is_write]
        if writes:
            candidates = writes
        else:
            demand_reads = [
                item
                for item in self._pending
                if item.source not in self._BACKGROUND_SOURCES
            ]
            background_reads = [
                item
                for item in self._pending
                if item.source in self._BACKGROUND_SOURCES
            ]
            candidates = demand_reads or background_reads
        selected = min(
            candidates,
            key=lambda item: (
                self._priority(item.source, item.client, is_write=item.is_write),
                item.queued_ns,
            ),
        )
        self._pending.remove(selected)
        return selected

    def _run_scheduler(self) -> None:
        while True:
            with self._queue_condition:
                while not self._pending:
                    self._queue_condition.wait()
                item = self._select_request()
            queue_wait_ms = (time.monotonic_ns() - item.queued_ns) / 1_000_000
            if self.events:
                self.events.emit(
                    event="downstream_served",
                    role="INFO",
                    source=item.source,
                    from_client=item.client,
                    queue_wait_ms=round(queue_wait_ms, 3),
                )
            try:
                with self._io_lock:
                    item.response = self._transact_physical(
                        item.request,
                        client=item.client,
                        source=item.source,
                        standard_modbus=item.standard_modbus,
                        is_write=item.is_write,
                        queue_wait_ms=queue_wait_ms,
                    )
            except Exception as exc:
                if self.events:
                    self.events.emit(
                        event="downstream_error",
                        role="ERROR",
                        source=item.source,
                        from_client=item.client,
                        error=str(exc),
                    )
                item.response = b""
                with self._io_lock:
                    self._reopen_serial("transaction_error")
            finally:
                item.done.set()

    def transact(
        self,
        req: bytes,
        *,
        client: str = "UNKNOWN",
        standard_modbus: bool = False,
        source: str | None = None,
        is_write: bool = False,
    ) -> bytes:
        source = source or ("SHINE" if client == "SHINE" else "PROD_TCP")
        item = DownstreamRequest(
            request=req,
            client=client,
            source=source,
            standard_modbus=standard_modbus,
            queued_ns=time.monotonic_ns(),
            done=threading.Event(),
            is_write=is_write,
        )
        with self._queue_condition:
            self._pending.append(item)
            self._queue_condition.notify()
        item.done.wait()
        return item.response

    def _transact_physical(
        self,
        req: bytes,
        *,
        client: str,
        source: str,
        standard_modbus: bool,
        is_write: bool = False,
        queue_wait_ms: float = 0.0,
    ) -> bytes:
        resp = b""
        physical_latency_ms = 0.0
        transaction_started = time.monotonic()
        timeout = self.rtimeout
        if self._ensure_serial():
            # Preserve complete asynchronous frames that arrived before this
            # request; only incomplete bytes remain for the response reader.
            assert self.ser is not None
            self.framer._read_available("pre_request_drain")
            self.framer.drain_complete_frames(
                lambda frame: self._report_async_frame(req, frame)
            )
            self.framer.last = time.perf_counter()
            tx_ns = time.monotonic_ns()
            if self.events:
                self.events.emit(
                    role="REQ",
                    source=source,
                    from_client=client,
                    monotonic_ns=tx_ns,
                    crc_ok=crc_ok(req),
                    hex=req.hex(),
                    **parse_rtu(req),
                )
            self.ser.write(req)
            self.ser.flush()
            physical_started = time.monotonic()
            if standard_modbus:
                resp = self.framer.read_standard_frame(
                    req,
                    timeout=timeout,
                    on_unmatched=lambda frame: self._report_async_frame(req, frame),
                    allow_unit_zero_wildcard=source == "SHINE",
                )
            else:
                resp = self.framer.read_matching(
                    lambda frame: (
                        crc_ok(frame)
                        and len(frame) >= 2
                        and (frame[0] == req[0] or (req[0] == 0 and frame[0] != 0))
                        and frame[1] in (req[1], req[1] | 0x80)
                    ),
                    timeout=timeout,
                    on_unmatched=lambda frame: self._report_async_frame(req, frame),
                )
            physical_latency_ms = (time.monotonic() - physical_started) * 1000
            if resp:
                self._consecutive_timeouts = 0
            elif standard_modbus:
                self._consecutive_timeouts += 1
                if self._consecutive_timeouts >= self._reopen_after_timeouts:
                    self._consecutive_timeouts = 0
                    self._reopen_serial("repeated_timeouts")
        if not resp and self.events:
            self.events.emit(
                event="downstream_timeout",
                role="WARN",
                to="INVERTER",
                source=source,
                from_client=client,
                function=req[1] if len(req) > 1 else None,
                timeout=timeout,
            )
        if self.events:
            self.events.emit(
                event="downstream_timing",
                role="INFO" if resp else "WARN",
                source=source,
                from_client=client,
                function=req[1] if len(req) > 1 else None,
                is_write=is_write,
                queue_wait_ms=round(queue_wait_ms, 3),
                physical_latency_ms=round(physical_latency_ms, 3),
                retry_count=0,
                total_ms=round((time.monotonic() - transaction_started) * 1000, 3),
            )
            self.events.emit(
                role="RSP",
                source=source,
                to_client=client,
                monotonic_ns=time.monotonic_ns(),
                crc_ok=crc_ok(resp),
                hex=(resp.hex() if resp else ""),
                **parse_rtu(resp or b""),
            )
        return resp


@dataclass
class _RefreshState:
    event: threading.Event
    snapshot: RegisterSnapshot | None = None
    error: str | None = None
    exception_response: bytes | None = None


class PhysicalModbusException(Exception):
    """A CRC-valid Modbus exception returned by the physical inverter."""

    def __init__(self, response: bytes) -> None:
        self.response = response
        super().__init__(f"physical Modbus exception: {response.hex()}")


@dataclass(frozen=True)
class WritePolicy:
    """Enable or disable physical FC06/FC10 writes per client source."""

    prod_tcp_enabled: bool = True
    dev_tcp_enabled: bool = True
    shine_enabled: bool = True

    def allows(self, source: str, key: RegisterKey) -> bool:
        if source == "PROD_TCP" and not self.prod_tcp_enabled:
            return False
        if source == "DEV_TCP" and not self.dev_tcp_enabled:
            return False
        if source == "SHINE" and not self.shine_enabled:
            return False
        return source in {"PROD_TCP", "DEV_TCP", "SHINE"}


class CacheGatewayService:
    """Serve TCP and Shine reads from one cache backed by ``Downstream``."""

    _ON_DEMAND_MAX_AGE = 10.0
    _NON_SHINE_MAX_AGE = 180.0
    _WAIT_TIMEOUT = 8.0
    _BACKGROUND_TARGET_AGE = 9.0

    def __init__(
        self,
        downstream: Downstream,
        *,
        events: EventHub | None = None,
        policies: tuple[CachePolicy, ...] | None = None,
        write_policy: WritePolicy | None = None,
        setup_observer: SetupPlanObserver | None = None,
    ) -> None:
        self.downstream = downstream
        self.events = events
        self.cache = RegisterCache()
        self.coordinator = PollCoordinator(self.cache, max_age=self._ON_DEMAND_MAX_AGE)
        self.policies = tuple(policies or ())
        self._policy_by_key = {policy.key: policy for policy in self.policies}
        self.write_policy = write_policy or WritePolicy()
        self.setup_observer = setup_observer
        self.shine_pattern = ShinePatternObserver(jitter_tolerance=0.75)
        self._lock = threading.Lock()
        self._inflight: dict[RegisterKey, _RefreshState] = {}
        self._write_condition = threading.Condition()
        self._write_active = False
        self._pending_writes: list[tuple[int, int, str]] = []
        self._write_sequence = 0
        self._next_due: dict[RegisterKey, float] = {
            policy.key: 0.0 for policy in self.policies
        }
        self._background_attempted: set[RegisterKey] = set()
        self._background_round_restart_at = 0.0
        self._last_errors: dict[RegisterKey, str | None] = {}
        self._stop = threading.Event()
        self._poller = threading.Thread(
            target=self._run_poller,
            name="growatt-cache-poller",
            daemon=True,
        )

    def _emit(self, event: str, **fields: object) -> None:
        if self.events:
            self.events.emit(event=event, **fields)

    def start(self, *, background: bool = True) -> None:
        if background:
            self._poller.start()

    def stop(self) -> None:
        self._stop.set()

    def observe_shine_request(self, request: bytes, *, at: float | None = None) -> None:
        """Learn the native block timing used by the physical Shine."""
        key = self._key_from_request(request)
        if key is None:
            return
        policy = self._policy_for(key)
        if policy is None and key.function != 0x20 and self.setup_observer is None:
            return
        physical_key = policy.key if policy is not None else key
        pattern_key = PatternKey(
            physical_key.function,
            physical_key.start,
            physical_key.count,
        )
        observed_at = time.monotonic() if at is None else at
        self.shine_pattern.observe(pattern_key, at=observed_at)
        if self.setup_observer is not None:
            self.setup_observer.observe(physical_key, at=observed_at)
            policy = self.setup_observer.recommended_policy(physical_key)
            if policy is not None:
                self._register_policy(policy)
        self._emit(
            "shine_pattern_observed",
            role="INFO",
            source="SHINE",
            function=physical_key.function,
            start=physical_key.start,
            count=physical_key.count,
            sequence_length=len(self.shine_pattern.history),
        )

    def _background_target_age(self, _policy: CachePolicy | None = None) -> float:
        """Target age for opportunistic read-only cache refreshes."""
        return self._BACKGROUND_TARGET_AGE

    def _register_policy(self, policy: CachePolicy) -> None:
        """Add or refine a setup-learned standard register block."""
        if policy.key.function not in (0x03, 0x04):
            return
        with self._lock:
            current = self._policy_by_key.get(policy.key)
            if current == policy:
                return
            if current is None:
                self.policies = (*self.policies, policy)
                self._next_due[policy.key] = 0.0
            else:
                self.policies = tuple(
                    policy if item.key == policy.key else item for item in self.policies
                )
            self._policy_by_key[policy.key] = policy
        self._emit(
            "setup_policy_updated",
            role="INFO",
            source="SETUP",
            function=policy.key.function,
            start=policy.key.start,
            count=policy.key.count,
            interval_s=policy.interval,
            max_age_s=policy.max_age,
        )

    def cache_status(self, *, now: float | None = None) -> dict[str, object]:
        """Return freshness metadata for future monitoring/EMS consumers."""
        current = time.monotonic() if now is None else now
        blocks: list[dict[str, object]] = []
        with self._lock:
            snapshots = {snapshot.key: snapshot for snapshot in self.cache.snapshots()}
            next_due = dict(self._next_due)
            errors = dict(self._last_errors)
        for policy in self.policies:
            snapshot = snapshots.get(policy.key)
            age = None if snapshot is None else max(0.0, current - snapshot.captured_at)
            blocks.append(
                {
                    "name": policy.name,
                    "function": policy.key.function,
                    "start": policy.key.start,
                    "count": policy.key.count,
                    "service_class": policy.service_class,
                    "target_interval_s": self._background_target_age(policy),
                    "hard_max_age_s": policy.max_age,
                    "age_s": age,
                    "fresh": age is not None and age <= policy.max_age,
                    "last_success": (
                        snapshot.captured_at if snapshot is not None else None
                    ),
                    "last_error": errors.get(policy.key),
                    "next_scheduled": next_due.get(policy.key),
                    "generation": (
                        snapshot.generation if snapshot is not None else None
                    ),
                }
            )
        return {"blocks": blocks}

    @staticmethod
    def _key_from_request(request: bytes) -> RegisterKey | None:
        if len(request) != 8 or not cache_crc_ok(request):
            return None
        if request[1] not in (0x03, 0x04):
            return None
        return RegisterKey(
            request[1],
            int.from_bytes(request[2:4], "big"),
            int.from_bytes(request[4:6], "big"),
        )

    @staticmethod
    def _written_holding_key(request: bytes) -> RegisterKey | None:
        if not cache_crc_ok(request) or len(request) < 8:
            return None
        if request[1] == 0x06 and len(request) == 8:
            return RegisterKey(3, int.from_bytes(request[2:4], "big"), 1)
        if request[1] != 0x10 or len(request) < 9:
            return None
        count = int.from_bytes(request[4:6], "big")
        byte_count = request[6]
        if not 1 <= count <= 125 or byte_count != count * 2:
            return None
        if len(request) != 9 + byte_count:
            return None
        return RegisterKey(3, int.from_bytes(request[2:4], "big"), count)

    def _policy_for(self, key: RegisterKey) -> CachePolicy | None:
        for policy in self.policies:
            if policy.key.contains(key):
                return policy
        return None

    def _physical_keys(self, key: RegisterKey) -> tuple[RegisterKey, ...]:
        """Return the native blocks that cover a requested range."""
        candidates = sorted(
            (
                policy.key
                for policy in self.policies
                if policy.key.function == key.function
                and policy.key.start < key.end
                and key.start < policy.key.end
            ),
            key=lambda candidate: candidate.start,
        )
        remaining = key.start
        selected: list[RegisterKey] = []
        for candidate in candidates:
            if candidate.start > remaining:
                break
            if candidate.end <= remaining:
                continue
            selected.append(candidate)
            remaining = min(key.end, candidate.end)
            if remaining == key.end:
                return tuple(selected)
        return (key,)

    def _max_age(self, key: RegisterKey, *, client: str) -> float:
        policy = self._policy_for(key)
        if policy is None:
            physical_keys = self._physical_keys(key)
            if physical_keys != (key,):
                policy_ages = tuple(
                    self._policy_by_key[physical_key].max_age
                    for physical_key in physical_keys
                )
                max_age = min(policy_ages)
                if client != "SHINE":
                    return max(max_age, self._NON_SHINE_MAX_AGE)
                return max_age
            return self._ON_DEMAND_MAX_AGE
        if client != "SHINE":
            return max(policy.max_age, self._NON_SHINE_MAX_AGE)
        return policy.max_age

    def _wire_read_request(self, key: RegisterKey) -> bytes:
        return add_crc(
            bytes([1, key.function])
            + key.start.to_bytes(2, "big")
            + key.count.to_bytes(2, "big")
        )

    @staticmethod
    def _decode_read_response(request: bytes, response: bytes) -> tuple[int, ...]:
        key = CacheGatewayService._key_from_request(request)
        if key is None:
            raise ValueError("invalid physical read request")
        if not cache_crc_ok(response):
            raise ValueError("physical response CRC invalid")
        if response[0] != request[0]:
            raise ValueError("physical response does not match request")
        if response[1] == (request[1] | 0x80):
            if len(response) != 5:
                raise ValueError("physical exception response length invalid")
            raise PhysicalModbusException(response)
        if response[1] != request[1]:
            raise ValueError("physical response does not match request")
        if len(response) != 5 + key.count * 2 or response[2] != key.count * 2:
            raise ValueError("physical response length does not match request")
        return tuple(
            int.from_bytes(response[offset : offset + 2], "big")
            for offset in range(3, 3 + key.count * 2, 2)
        )

    def _refresh(
        self,
        key: RegisterKey,
        *,
        client: str,
        source: str,
        reason: str,
    ) -> RegisterSnapshot | None:
        request = self._wire_read_request(key)
        started = time.monotonic()
        self._emit(
            "cache_physical_refresh_start",
            role="INFO",
            client=client,
            source=source,
            reason=reason,
            function=key.function,
            start=key.start,
            count=key.count,
        )
        response = self.downstream.transact(
            request,
            client=client,
            source=source,
            standard_modbus=True,
        )
        if not response:
            raise TimeoutError("physical read timeout")
        words = self._decode_read_response(request, response)
        captured_at = time.monotonic()
        with self._lock:
            snapshot = self.cache.put_block(
                key,
                words,
                captured_at=captured_at,
                source_transaction=f"{source}:{key.function:02x}:{key.start}:{key.count}",
            )
            self._last_errors[key] = None
        self._emit(
            "cache_physical_refresh_complete",
            role="INFO",
            client=client,
            source=source,
            function=key.function,
            start=key.start,
            count=key.count,
            generation=snapshot.generation,
            snapshot_id=snapshot.snapshot_id,
            duration_ms=round((time.monotonic() - started) * 1000, 3),
        )
        return snapshot

    def _write_priority(self, source: str) -> int:
        return Downstream._WRITE_PRIORITIES.get(source, len(Downstream._WRITE_PRIORITIES))

    def _acquire_write_slot(self, source: str) -> None:
        """Queue a write ticket so production wins over development and Shine."""
        with self._write_condition:
            ticket = (self._write_priority(source), self._write_sequence, source)
            self._write_sequence += 1
            self._pending_writes.append(ticket)
            while self._write_active or min(self._pending_writes) != ticket:
                self._write_condition.wait()
            self._pending_writes.remove(ticket)
            self._write_active = True

    def _release_write_slot(self) -> None:
        with self._write_condition:
            self._write_active = False
            self._write_condition.notify_all()

    def _read_words(
        self,
        key: RegisterKey,
        *,
        client: str,
        source: str,
        now: float,
        force_refresh: bool = False,
        allow_during_write: bool = False,
    ) -> tuple[CachedRead | None, str | None]:
        if not allow_during_write:
            with self._write_condition:
                while self._write_active or self._pending_writes:
                    self._write_condition.wait()
        max_age = self._max_age(key, client=client)
        physical_keys = self._physical_keys(key)
        allow_composed = len(physical_keys) > 1
        with self._lock:
            cached = self.cache.read(
                key,
                now=now,
                max_age=max_age,
                allow_composed=allow_composed,
                allow_mixed_snapshots=allow_composed,
            )
        if cached is not None and not force_refresh:
            self._emit(
                "cache_hit",
                role="INFO",
                client=client,
                source=source,
                function=key.function,
                start=key.start,
                count=key.count,
                age_ms=round(cached.age * 1000, 3),
                generation=cached.generation,
                snapshot_id=cached.snapshot_ids,
            )
            return cached, None

        self._emit(
            "cache_miss",
            role="INFO",
            client=client,
            source=source,
            function=key.function,
            start=key.start,
            count=key.count,
            physical_blocks=[
                {"start": block.start, "count": block.count} for block in physical_keys
            ],
        )
        for physical_key in physical_keys:
            with self._lock:
                block_cached = self.cache.read(physical_key, now=now, max_age=max_age)
                state = self._inflight.get(physical_key)
                needs_refresh = force_refresh or block_cached is None
                owner = needs_refresh and state is None
                if owner:
                    state = _RefreshState(threading.Event())
                    self._inflight[physical_key] = state
                elif needs_refresh and state is not None:
                    self._emit(
                        "cache_coalesced",
                        role="INFO",
                        client=client,
                        source=source,
                        function=key.function,
                        start=key.start,
                        count=key.count,
                    )

            if not needs_refresh:
                continue
            if owner:
                try:
                    state.snapshot = self._refresh(
                        physical_key,
                        client=client,
                        source=source,
                        reason="cache_miss_or_stale",
                    )
                except Exception as exc:
                    state.error = str(exc)
                    if isinstance(exc, PhysicalModbusException):
                        state.exception_response = exc.response
                    with self._lock:
                        self._last_errors[physical_key] = state.error
                    self._emit(
                        "cache_physical_refresh_failed",
                        role="ERROR",
                        client=client,
                        source=source,
                        function=physical_key.function,
                        start=physical_key.start,
                        count=physical_key.count,
                        error=state.error,
                    )
                finally:
                    with self._lock:
                        self._inflight.pop(physical_key, None)
                        state.event.set()
            elif not state.event.wait(self._WAIT_TIMEOUT):
                return None, "cache refresh wait timeout"
            if state.exception_response is not None:
                raise PhysicalModbusException(state.exception_response)
            if state.error:
                return None, state.error

        with self._lock:
            cached = self.cache.read(
                key,
                now=time.monotonic(),
                max_age=max_age,
                allow_composed=allow_composed,
                allow_mixed_snapshots=allow_composed,
            )
        if cached is None:
            return None, "refresh did not produce a usable snapshot"
        return cached, None

    def handle_standard_request(
        self,
        request: bytes,
        *,
        client: str,
        source: str,
        now: float | None = None,
    ) -> GatewayResult:
        key = self._key_from_request(request)
        if key is None or not 1 <= key.count <= 125:
            return GatewayResult("failed", client, reason="invalid_standard_read")
        request_now = time.monotonic() if now is None else now
        if client == "SHINE":
            self.observe_shine_request(request, at=request_now)
        elif self.setup_observer is not None:
            self.setup_observer.observe(key, at=request_now)
        try:
            cached, error = self._read_words(
                key,
                client=client,
                source=source,
                now=request_now,
            )
        except PhysicalModbusException as exc:
            return GatewayResult(
                "failed",
                client,
                response=exc.response,
                reason="physical_exception",
            )
        if cached is None:
            return GatewayResult("failed", client, reason=error or "read failed")
        return GatewayResult(
            "served",
            client,
            read=cached,
            response=build_read_response(request, cached.words),
            reason="cache",
        )

    @staticmethod
    def _write_key(request: bytes) -> RegisterKey | None:
        if not cache_crc_ok(request) or len(request) < 8:
            return None
        function = request[1]
        if function == 0x06 and len(request) == 8:
            return RegisterKey(3, int.from_bytes(request[2:4], "big"), 1)
        if function != 0x10 or len(request) < 11:
            return None
        count = int.from_bytes(request[4:6], "big")
        byte_count = request[6]
        if not 1 <= count <= 125 or byte_count != count * 2:
            return None
        if len(request) != 9 + byte_count:
            return None
        return RegisterKey(3, int.from_bytes(request[2:4], "big"), count)

    @staticmethod
    def _exception_response(request: bytes, code: int) -> bytes:
        return add_crc(bytes([request[0], request[1] | 0x80, code]))

    def _schedule_refresh_after_write(
        self, keys: Iterable[RegisterKey], *, immediate: bool
    ) -> None:
        """Re-arm affected configured blocks after write invalidation/readback."""
        keys = tuple(keys)
        if not keys:
            return
        now = time.monotonic()
        scheduled: list[dict[str, object]] = []
        with self._lock:
            for key in keys:
                policy = self._policy_by_key.get(key)
                if policy is None:
                    continue
                due_at = (
                    now
                    if immediate
                    else now
                )
                self._next_due[key] = due_at
                scheduled.append(
                    {
                        "function": key.function,
                        "start": key.start,
                        "count": key.count,
                        "due_in_ms": round(max(0.0, due_at - now) * 1000, 3),
                    }
                )
        if scheduled:
            self._emit(
                "cache_refresh_scheduled_after_write",
                role="INFO" if not immediate else "WARN",
                immediate=immediate,
                blocks=scheduled,
            )

    def _invalidate_before_write(
        self,
        key: RegisterKey,
        *,
        client: str,
        source: str,
    ) -> tuple[RegisterKey, ...]:
        """Mark affected snapshots stale before a physical write executes."""
        with self._lock:
            invalidated = self.cache.mark_stale_overlapping(
                key, captured_at=time.monotonic() - 30.0
            )
        self._emit(
            "cache_invalidated_before_write",
            role="INFO",
            client=client,
            source=source,
            function=key.function,
            start=key.start,
            count=key.count,
            blocks=len(invalidated),
        )
        self._schedule_refresh_after_write(invalidated, immediate=True)
        return invalidated

    @staticmethod
    def _valid_physical_exception(
        response: bytes,
        request: bytes,
        *,
        allow_unit_zero_wildcard: bool = False,
    ) -> bool:
        return (
            len(response) == 5
            and (
                response[0] == request[0]
                or (allow_unit_zero_wildcard and request[0] == 0)
            )
            and response[1] == (request[1] | 0x80)
            and cache_crc_ok(response)
        )

    def handle_write_request(
        self,
        request: bytes,
        *,
        client: str,
        source: str,
    ) -> GatewayResult:
        """Execute one enabled physical FC06/FC10 write."""
        if len(request) < 2 or request[1] not in (0x06, 0x10):
            return GatewayResult("failed", client, reason="unsupported_write_function")
        key = self._write_key(request)
        if key is None:
            response = self._exception_response(request, 0x03)
            return GatewayResult(
                "failed", client, response=response, reason="malformed_write"
            )
        if not self.write_policy.allows(source, key):
            response = self._exception_response(request, 0x02)
            self._emit(
                "tcp_write_denied",
                role="WARN",
                client=client,
                source=source,
                function=request[1],
                start=key.start,
                count=key.count,
            )
            return GatewayResult(
                "failed", client, response=response, reason="write_denied"
            )

        values = (
            (int.from_bytes(request[4:6], "big"),)
            if request[1] == 0x06
            else tuple(
                int.from_bytes(request[offset : offset + 2], "big")
                for offset in range(7, 7 + key.count * 2, 2)
            )
        )
        self._invalidate_before_write(key, client=client, source=source)
        self._acquire_write_slot(source)
        try:
            started = time.monotonic()
            response = self.downstream.transact(
                request,
                client=client,
                source=source,
                standard_modbus=True,
                is_write=True,
            )
            write_reason: str
            client_response: bytes
            physical_success = False
            if not response:
                client_response = self._exception_response(request, 0x0B)
                write_reason = "physical_timeout"
                self._emit(
                    "tcp_write_failed",
                    role="ERROR",
                    client=client,
                    source=source,
                    function=request[1],
                    start=key.start,
                    count=key.count,
                    values=values,
                    reason=write_reason,
                )
            elif response[0] != request[0] or not cache_crc_ok(response):
                client_response = self._exception_response(request, 0x0B)
                write_reason = "invalid_physical_write_response"
                self._emit(
                    "tcp_write_failed",
                    role="ERROR",
                    client=client,
                    source=source,
                    function=request[1],
                    start=key.start,
                    count=key.count,
                    values=values,
                    reason=write_reason,
                )
            elif self._valid_physical_exception(response, request):
                client_response = response
                write_reason = "physical_exception"
                self._emit(
                    "tcp_write_exception",
                    role="WARN",
                    client=client,
                    source=source,
                    function=request[1],
                    start=key.start,
                    count=key.count,
                    response=response.hex(),
                )
            elif find_standard_response(response, request) is None:
                client_response = self._exception_response(request, 0x0B)
                write_reason = "invalid_physical_write_response"
                self._emit(
                    "tcp_write_failed",
                    role="ERROR",
                    client=client,
                    source=source,
                    function=request[1],
                    start=key.start,
                    count=key.count,
                    values=values,
                    reason=write_reason,
                )
            else:
                client_response = response
                write_reason = "physical_write_readback_confirmed"
                physical_success = True

            if physical_success:
                self._emit(
                    "tcp_write_acknowledged",
                    role="INFO",
                    client=client,
                    source=source,
                    function=request[1],
                    start=key.start,
                    count=key.count,
                    values=values,
                    physical_success=True,
                    cache_refresh_pending=True,
                    duration_ms=round((time.monotonic() - started) * 1000, 3),
                    generation=self.cache.latest_generation,
                )
                return GatewayResult(
                    "served",
                    client,
                    response=client_response,
                    reason="physical_write_acknowledged",
                )
            return GatewayResult(
                "failed", client, response=client_response, reason=write_reason
            )
        finally:
            self._release_write_slot()

    def handle_on_demand_request(
        self,
        request: bytes,
        *,
        client: str,
        source: str,
        now: float | None = None,
    ) -> GatewayResult:
        """Forward a valid Modbus request that has no cache representation."""
        del now
        is_write = request[1] in (0x06, 0x10)
        write_key = self._write_key(request) if is_write else None
        if is_write:
            if write_key is None or not self.write_policy.allows(source, write_key):
                response = self._exception_response(request, 0x02)
                self._emit(
                    "tcp_write_denied",
                    role="WARN",
                    client=client,
                    source=source,
                    reason="write_policy",
                )
                return GatewayResult(
                    "failed", client, response=response, reason="write_denied"
                )
            self._invalidate_before_write(
                write_key, client=client, source=source
            )
            self._acquire_write_slot(source)
        try:
            response = self.downstream.transact(
                request,
                client=client,
                source=source,
                # Growatt's unit-0 discovery receives a unit-1 response.
                standard_modbus=(
                    standard_response_spec(request) is not None and request[0] != 0
                ),
                is_write=is_write,
            )
            if response:
                physical_exception = is_write and self._valid_physical_exception(
                    response,
                    request,
                    allow_unit_zero_wildcard=request[0] == 0,
                )
                write_ack = (
                    is_write
                    and not physical_exception
                    and find_standard_response(
                        response,
                        request,
                        allow_unit_zero_wildcard=request[0] == 0,
                    )
                    is not None
                )
                if is_write and write_key is not None:
                    self._emit(
                        "on_demand_write_forwarded",
                        role="INFO",
                        client=client,
                        source=source,
                        cache_refresh_pending=True,
                        **parse_rtu(request),
                    )
                self._emit(
                    "on_demand_physical",
                    role="INFO",
                    client=client,
                    source=source,
                    **parse_rtu(request),
                )
                return GatewayResult(
                    "served" if (not is_write or write_ack) else "failed",
                    client,
                    response=response,
                    reason=(
                        "on_demand"
                        if not is_write
                        else (
                            "physical_exception"
                            if physical_exception
                            else (
                            "on_demand"
                                if write_ack
                                else "invalid_physical_write_response"
                            )
                        )
                    ),
                )
            return GatewayResult(
                "failed", client, reason="on-demand timeout"
            )
        finally:
            if is_write:
                self._release_write_slot()

    def handle_request(
        self,
        request: bytes,
        *,
        client: str,
        source: str,
        now: float | None = None,
    ) -> GatewayResult:
        """Route one valid Modbus request through the shared client path."""
        if not cache_crc_ok(request) or len(request) < 2:
            return GatewayResult("failed", client, reason="invalid_modbus_frame")
        function = request[1]
        if function in (0x06, 0x10):
            return self.handle_write_request(request, client=client, source=source)
        if (
            function in (0x03, 0x04)
            and request[0] != 0
            and standard_response_spec(request) is not None
        ):
            return self.handle_standard_request(
                request,
                client=client,
                source=source,
                now=now,
            )
        return self.handle_on_demand_request(
            request,
            client=client,
            source=source,
            now=now,
        )

    def _oldest_background_target(self, now: float) -> tuple[str, CachePolicy | None, float] | None:
        """Choose the stalest configured block once it reaches the target age."""
        with self._lock:
            snapshots = {snapshot.key: snapshot for snapshot in self.cache.snapshots()}
            policies = tuple(self.policies)
            next_due = dict(self._next_due)
            attempted = set(self._background_attempted)
            round_restart_at = self._background_round_restart_at
        if now < round_restart_at:
            return None
        candidates: list[tuple[float, int, str, CachePolicy | None]] = []
        for policy in policies:
            if policy.key in attempted:
                continue
            if now < next_due.get(policy.key, 0.0):
                continue
            snapshot = snapshots.get(policy.key)
            age = float("inf") if snapshot is None else max(0.0, now - snapshot.captured_at)
            if age >= self._background_target_age(policy):
                candidates.append((age, policy.priority, "standard", policy))
        if not candidates:
            return None
        age, _priority, kind, policy = max(candidates, key=lambda item: (item[0], -item[1]))
        return kind, policy, age

    def _next_background_deadline(self, now: float) -> float | None:
        """Return when the next cache block reaches the target age or retry time."""
        deadlines: list[float] = []
        with self._lock:
            snapshots = {snapshot.key: snapshot for snapshot in self.cache.snapshots()}
            attempted = set(self._background_attempted)
            round_restart_at = self._background_round_restart_at
            for policy in self.policies:
                if policy.key in attempted:
                    continue
                retry = self._next_due.get(policy.key, 0.0)
                captured = snapshots.get(policy.key)
                age_due = (
                    now
                    if captured is None
                    else captured.captured_at + self._background_target_age(policy)
                )
                deadlines.append(max(retry, age_due))
            if round_restart_at > now:
                deadlines.append(round_restart_at)
        return min(deadlines) if deadlines else None

    def _run_poller(self) -> None:
        while not self._stop.is_set():
            # Submit one block only; the shared scheduler arbitrates the next
            # physical transaction before this thread selects another block.
            now = time.monotonic()
            target = self._oldest_background_target(now)
            if target is not None:
                _kind, policy, _age = target
                assert policy is not None
                _, error = self._read_words(
                    policy.key,
                    client="CACHE",
                    source="BACKGROUND",
                    now=now,
                    force_refresh=True,
                )
                with self._lock:
                    if error is not None:
                        self._background_attempted.add(policy.key)
                    else:
                        self._background_attempted.add(policy.key)
                    if self._background_attempted == {
                        configured.key for configured in self.policies
                    }:
                        self._background_round_restart_at = (
                            time.monotonic() + self._BACKGROUND_TARGET_AGE
                        )
                        self._background_attempted.clear()
                continue
            deadline = self._next_background_deadline(now)
            if deadline is None:
                self._stop.wait(0.5)
                continue
            self._stop.wait(min(0.5, max(0.02, deadline - time.monotonic())))


class ShineEndpoint(threading.Thread):
    def __init__(
        self,
        dev: str,
        baud: int,
        fmt: str,
        downstream: Downstream,
        events: Optional[EventHub] = None,
        write_policy: WritePolicy | None = None,
        virtual_adapter: ShineVirtualInverterAdapter | None = None,
    ):
        super().__init__(daemon=True)
        self.dev = dev
        self._serial_paths = Downstream._discover_serial_paths(dev)
        self._active_serial_path = self._serial_paths[0]
        self.baud = baud
        self.fmt = fmt
        self.ds = downstream
        self.events = events
        self.write_policy = write_policy or WritePolicy()
        self.virtual_adapter = virtual_adapter
        self.ser: Optional[serial.Serial] = None
        self.framer: Optional[RTUFramer] = None
        self._online = False
        self._write_lock = threading.Lock()
        self.ds.set_async_frame_handler(self._forward_async_frame)

    def _report_unframed(self, data: bytes, reason: str) -> None:
        """Record Shine serial bytes that do not form a CRC-valid RTU frame."""
        preview = data[:512]
        if self.events:
            self.events.emit(
                event="shine_unframed_bytes",
                classification="possibly_non_modbus",
                role="WIRE",
                source="SHINE",
                direction="shine_to_broker",
                reason=reason,
                length=len(data),
                preview_hex=preview.hex(),
                preview_truncated=len(preview) != len(data),
            )

    def _forward_async_frame(self, frame: bytes) -> None:
        """Forward unsolicited inverter frames to the physical Shine."""
        try:
            with self._write_lock:
                if self.ser is None or not self.ser.is_open:
                    if self.events:
                        self.events.emit(
                            event="async_frame_no_shine",
                            role="WARN",
                            source="INVERTER",
                            hex=frame.hex(),
                        )
                    return
                self.ser.write(frame)
                self.ser.flush()
        except (serial.SerialException, OSError, ValueError) as exc:
            if self.events:
                self.events.emit(
                    event="shine_serial_error",
                    role="WARN",
                    port=self.dev,
                    error=str(exc),
                )
            self._close_port()
            return
        if self.events:
            self.events.emit(
                event="async_frame_forwarded",
                role="INFO",
                source="INVERTER",
                to_client="SHINE",
                hex=frame.hex(),
                **parse_rtu(frame),
            )

    def _open_port(self) -> None:
        databits = int(self.fmt[0])
        parity = self.fmt[1].upper()
        stop = int(self.fmt[2])
        py_par = {
            "N": serial.PARITY_NONE,
            "E": serial.PARITY_EVEN,
            "O": serial.PARITY_ODD,
        }[parity]
        py_stp = {1: serial.STOPBITS_ONE, 2: serial.STOPBITS_TWO}[stop]
        last_error: Exception | None = None
        for path in self._serial_paths:
            try:
                self.ser = serial.Serial(
                    path,
                    self.baud,
                    bytesize=databits,
                    parity=py_par,
                    stopbits=py_stp,
                    timeout=0,
                )
            except (serial.SerialException, OSError) as exc:
                last_error = exc
                continue
            self._active_serial_path = path
            break
        else:
            assert last_error is not None
            raise last_error
        bits_per_char = 1 + databits + stop + (0 if parity == "N" else 1)
        self.framer = RTUFramer(
            self.ser,
            bits_per_char / self.baud,
            on_unframed=self._report_unframed,
        )
        self._online = True
        if self.virtual_adapter is not None:
            self.virtual_adapter.observe_hotplug()
        if self.events:
            self.events.emit(
                event="shine_online",
                role="SYS",
                port=self._active_serial_path,
                baud=self.baud,
                fmt=self.fmt,
            )
        # Logging is handled via EventHub/WireLogger; no direct stdout prints here

    def _close_port(self) -> None:
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None
        self.framer = None
        if self._online and self.events:
            self.events.emit(event="shine_offline", role="SYS", port=self.dev)
        if self.virtual_adapter is not None:
            self.virtual_adapter.observe_disconnect()
        self._online = False
        # Logging is handled via EventHub/WireLogger; no direct stdout prints here

    def run(self):
        while True:
            if self.ser is not None and not os.path.exists(self.dev):
                self._close_port()
                time.sleep(1.0)
                continue
            if not self.ser or not self.framer:
                try:
                    self._open_port()
                except Exception as exc:
                    if self.events:
                        self.events.emit(
                            event="shine_open_failed",
                            role="WARN",
                            port=self._active_serial_path,
                            error=str(exc),
                        )
                    time.sleep(5.0)
                    continue
            try:
                req = self.framer.read_frame(timeout=10.0)
                if not req:
                    continue
                if len(req) < 4 or not crc_ok(req):
                    if self.events:
                        self.events.emit(
                            role="DROP",
                            from_client="SHINE",
                            reason="bad_crc",
                            hex=req.hex(),
                        )
                    continue

                function = req[1]
                is_write = function in (0x06, 0x10)
                write_key = (
                    CacheGatewayService._write_key(req)
                    if is_write
                    else None
                )
                writes_allowed = not is_write or (
                    write_key is not None
                    and self.write_policy.allows("SHINE", write_key)
                )
                disposition = "forwarded" if writes_allowed else "write_denied"
                standard_request = writes_allowed and standard_response_spec(req) is not None

                if self.events:
                    self.events.emit(
                        event="shine_request",
                        role="REQ",
                        source="SHINE",
                        from_client="SHINE",
                        disposition=disposition,
                        crc_ok=True,
                        hex=req.hex(),
                        **parse_rtu(req),
                    )

                if self.virtual_adapter is not None:
                    result = self.virtual_adapter.handle_request(
                        req, now=time.monotonic()
                    )
                    resp = result.response or b""
                    if result.status == "quarantined" and not resp:
                        resp = add_crc(bytes([req[0], function | 0x80, 0x01]))
                    if (
                        result.status == "failed"
                        and not resp
                        and not (result.reason and result.reason.startswith("physical"))
                    ):
                        resp = add_crc(bytes([req[0], function | 0x80, 0x0B]))
                    if self.events:
                        self.events.emit(
                            event="shine_virtual_result",
                            role="INFO" if result.status == "served" else "WARN",
                            source="SHINE",
                            from_client="SHINE",
                            status=result.status,
                            reason=result.reason,
                            **parse_rtu(req),
                        )
                elif not writes_allowed:
                    resp = add_crc(bytes([req[0], function | 0x80, 0x01]))
                    if self.events:
                        self.events.emit(
                            event="write_policy_blocked",
                            role="DROP",
                            source="SHINE",
                            from_client="SHINE",
                            disposition=disposition,
                            hex=req.hex(),
                            **parse_rtu(req),
                        )
                else:
                    started = time.monotonic_ns()
                    resp = self.ds.transact(
                        req,
                        client="SHINE",
                        source="SHINE",
                        standard_modbus=standard_request,
                        is_write=is_write,
                    )
                    if self.events:
                        self.events.emit(
                            event="shine_forwarded",
                            role="INFO",
                            source="SHINE",
                            from_client="SHINE",
                            disposition=disposition,
                            duration_ms=round(
                                (time.monotonic_ns() - started) / 1_000_000, 3
                            ),
                            **parse_rtu(req),
                        )
                if resp:
                    with self._write_lock:
                        self.ser.write(resp)
                        self.ser.flush()
                    if self.events:
                        self.events.emit(
                            event="shine_response",
                            role="RSP",
                            source="SHINE",
                            to_client="SHINE",
                            disposition=disposition,
                            crc_ok=crc_ok(resp),
                            hex=resp.hex(),
                            **parse_rtu(resp),
                        )
                else:
                    if self.events:
                        self.events.emit(
                            event="downstream_timeout",
                            role="WARN",
                            to="INVERTER",
                            from_client="SHINE",
                        )
            except (serial.SerialException, OSError) as exc:
                if self.events:
                    self.events.emit(
                        event="shine_serial_error",
                        role="WARN",
                        port=self.dev,
                        error=str(exc),
                    )
                self._close_port()
                time.sleep(2.0)
            except Exception as exc:
                if self.events:
                    self.events.emit(
                        event="shine_unhandled_error",
                        role="ERROR",
                        port=self.dev,
                        error=str(exc),
                    )
                self._close_port()
                time.sleep(2.0)


class TCPServer(threading.Thread):
    def __init__(
        self,
        bind_host: str,
        bind_port: int,
        downstream: Downstream,
        events: EventHub | None = None,
        source: str = "PROD_TCP",
        gateway: CacheGatewayService | None = None,
    ):
        super().__init__(daemon=True)
        self.addr = (bind_host, bind_port)
        self.ds = downstream
        self.events = events
        self.source = source
        self.gateway = gateway
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(self.addr)
        self.sock.listen(8)

    def run(self):
        while True:
            conn, _ = self.sock.accept()
            threading.Thread(target=self.handle, args=(conn,), daemon=True).start()

    def handle(self, conn: socket.socket):
        try:
            peer_info = conn.getpeername()
            if isinstance(peer_info, tuple) and len(peer_info) >= 2:
                peer = f"TCP:{peer_info[0]}:{peer_info[1]}"
            else:
                peer = "TCP:LOCAL"
            conn.settimeout(3.0)
            while True:
                hdr = self._recv_exact(conn, 7)
                if not hdr:
                    break
                tid = hdr[0:2]
                pid = hdr[2:4]
                length = int.from_bytes(hdr[4:6], "big")
                uid = hdr[6]
                pdu = self._recv_exact(conn, length - 1)
                if not pdu:
                    break
                is_write = bool(pdu) and pdu[0] in (0x06, 0x10)
                rtu_req = add_crc(bytes([uid]) + pdu)
                if self.gateway is not None:
                    result = self.gateway.handle_request(
                        rtu_req, client=peer, source=self.source
                    )
                    rtu_resp = result.response or b""
                    if result.status != "served" and self.events:
                        self.events.emit(
                            event="tcp_gateway_failure",
                            role="WARN",
                            source=self.source,
                            from_client=peer,
                            reason=result.reason,
                        )
                else:
                    if is_write:
                        rtu_resp = add_crc(bytes([uid, pdu[0] | 0x80, 0x01]))
                        if self.events:
                            self.events.emit(
                                event="tcp_write_denied",
                                role="WARN",
                                source=self.source,
                                from_client=peer,
                                reason="write_policy_gateway_required",
                            )
                    else:
                        rtu_resp = self.ds.transact(
                            rtu_req,
                            client=peer,
                            source=self.source,
                            standard_modbus=True,
                            is_write=False,
                        )
                if not rtu_resp or len(rtu_resp) < 4 or not crc_ok(rtu_resp):
                    break
                uid2 = rtu_resp[0]
                pdu2 = rtu_resp[1:-2]
                rsp_len = len(pdu2) + 1
                mbap = tid + pid + rsp_len.to_bytes(2, "big") + bytes([uid2])
                response = mbap + pdu2
                conn.sendall(response)
                if is_write:
                    continue
                if rtu_resp[1] & 0x80:
                    continue
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    @staticmethod
    def _recv_exact(conn: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                return b""
            buf += chunk
        return buf


def main():
    ap = argparse.ArgumentParser(
        description="Growatt broker: Shine serial + Modbus-TCP -> single RTU master"
    )
    ap.add_argument(
        "--inverter",
        required=False,
        default=None,
        help="Downstream serial device (or provide it in --config)",
    )
    ap.add_argument(
        "--shine",
        required=False,
        default=None,
        help="Optional upstream ShineWiFi-X serial device (omit if not present)",
    )
    ap.add_argument("--inv-baud", type=int, help="Inverter baudrate")
    ap.add_argument("--inv-bytes", default=None, help="Inverter format, e.g. 8E1")
    ap.add_argument("--shine-baud", type=int, help="Shine baudrate")
    ap.add_argument("--shine-bytes", default=None, help="Shine format, e.g. 8E1")
    ap.add_argument(
        "--shine-policy",
        choices=("enabled", "disabled"),
        default=None,
        help="Enable or disable FC06/FC10 writes from Shine",
    )
    ap.add_argument(
        "--mode",
        choices=(
            "legacy",
            "cache",
            "cache+shine",
            "cache+shine-direct",
        ),
        default=None,
        help="Broker data-plane mode; cache modes are opt-in",
    )
    ap.add_argument(
        "--config",
        default=None,
        help="Installation JSON configuration",
    )
    ap.add_argument(
        "--operation-mode",
        choices=("live", "setup"),
        default=None,
        help="Use the approved config, or observe and learn a candidate config",
    )
    ap.add_argument(
        "--setup-export",
        default=None,
        help="Candidate config path written after SIGUSR2 in setup mode",
    )
    ap.add_argument(
        "--baud", type=int, default=None, help="Default baud if side-specific not set"
    )
    ap.add_argument(
        "--bytes", default=None, help="Default serial format if side-specific not set"
    )
    ap.add_argument(
        "--tcp",
        default="0.0.0.0:5020",
        help="Bind host:port for primary Modbus-TCP server (use '-' to disable)",
    )
    ap.add_argument(
        "--tcp-alt",
        default=None,
        help="Optional secondary Modbus-TCP server for lab/testing (use '-' to disable)",
    )
    ap.add_argument(
        "--sniff",
        default=None,
        help="Optional host:port for streaming JSONL sniff feed (use '-' to disable)",
    )
    ap.add_argument(
        "--rtimeout", type=float, default=None, help="RTU read timeout seconds"
    )
    ap.add_argument(
        "--reopen-after-timeouts",
        type=int,
        default=None,
        help="Reopen the inverter serial port after this many standard read timeouts",
    )
    for source in ("prod", "dev"):
        ap.add_argument(
            f"--{source}-tcp-writes",
            choices=("enabled", "disabled"),
            default=None,
            help=f"Enable or disable FC06/FC10 writes on {source.upper()}_TCP",
        )
    ap.add_argument(
        "--log",
        default="/var/log/growatt_broker.jsonl",
        help="JSONL log path (use '-' to disable)",
    )
    args = ap.parse_args()

    installation_config: InstallationConfig | None = None
    if args.config:
        try:
            installation_config = load_installation_config(args.config)
        except ConfigurationError as exc:
            ap.error(str(exc))

    if installation_config is not None:
        if args.inverter is None:
            args.inverter = installation_config.inverter_transport.device
        if args.shine is None:
            args.shine = installation_config.logger_transport.device
        if args.inv_baud is None:
            args.inv_baud = installation_config.inverter_transport.baud
        if args.inv_bytes is None:
            args.inv_bytes = installation_config.inverter_transport.bytes
        if args.shine_baud is None:
            args.shine_baud = installation_config.logger_transport.baud
        if args.shine_bytes is None:
            args.shine_bytes = installation_config.logger_transport.bytes
        if args.mode is None:
            args.mode = installation_config.mode
        if args.operation_mode is None:
            args.operation_mode = installation_config.operation_mode

    args.mode = args.mode or "legacy"
    args.operation_mode = args.operation_mode or "live"
    args.baud = args.baud or 9600
    args.bytes = args.bytes or "8E1"
    args.rtimeout = args.rtimeout if args.rtimeout is not None else 0.85
    args.reopen_after_timeouts = (
        args.reopen_after_timeouts if args.reopen_after_timeouts is not None else 10
    )
    configured_writes = installation_config.write_policy if installation_config else {}
    args.shine_policy = args.shine_policy or configured_writes.get("shine", "disabled")
    args.prod_tcp_writes = args.prod_tcp_writes or configured_writes.get(
        "prod_tcp", "enabled"
    )
    args.dev_tcp_writes = args.dev_tcp_writes or configured_writes.get(
        "dev_tcp", "enabled"
    )
    if args.shine_policy not in {"enabled", "disabled"}:
        ap.error(f"invalid Shine write policy: {args.shine_policy}")
    if args.prod_tcp_writes not in {"enabled", "disabled"}:
        ap.error(f"invalid production TCP write policy: {args.prod_tcp_writes}")
    if args.dev_tcp_writes not in {"enabled", "disabled"}:
        ap.error(f"invalid development TCP write policy: {args.dev_tcp_writes}")
    if args.inverter is None:
        ap.error("--inverter is required unless it is present in --config")
    if args.operation_mode == "setup" and installation_config is None:
        ap.error("setup mode requires --config")
    if args.mode != "legacy" and installation_config is None:
        ap.error("cache modes require --config with an explicit poll_plan")

    if (
        args.mode
        in {
            "cache+shine",
            "cache+shine-direct",
        }
        and not args.shine
    ):
        ap.error(f"{args.mode} mode requires --shine")
    setup_observer = (
        SetupPlanObserver(installation_config)
        if args.operation_mode == "setup" and installation_config is not None
        else None
    )

    inv_baud = args.inv_baud or args.baud
    inv_bytes = args.inv_bytes or args.bytes
    sh_baud = args.shine_baud or args.baud
    sh_bytes = args.shine_bytes or args.bytes

    sinks: List[EventSink] = []
    file_logger = WireLogger(args.log)
    if file_logger.enabled():
        sinks.append(file_logger)

    sniff_desc = None
    if args.sniff and args.sniff not in {"", "-"}:
        try:
            sniff_host, sniff_port = parse_host_port(args.sniff)
        except ValueError as exc:
            ap.error(str(exc))
        sniffer = SnifferRelay(sniff_host, sniff_port)
        sniffer.start()
        sinks.append(sniffer)
        sniff_desc = f"{sniff_host}:{sniff_port}"

    events = EventHub(sinks)
    ds = None
    gateway = None
    virtual_adapter = None
    shine = None
    write_policy = WritePolicy(
        prod_tcp_enabled=args.prod_tcp_writes == "enabled",
        dev_tcp_enabled=args.dev_tcp_writes == "enabled",
        shine_enabled=args.shine_policy == "enabled",
    )
    ds = Downstream(
        args.inverter,
        inv_baud,
        inv_bytes,
        rtimeout=args.rtimeout,
        reopen_after_timeouts=args.reopen_after_timeouts,
        events=events,
    )
    if args.mode != "legacy":
        gateway = CacheGatewayService(
            ds,
            events=events,
            policies=(
                installation_config.policies()
                if installation_config is not None
                else None
            ),
            write_policy=write_policy,
            setup_observer=setup_observer,
        )
        events.emit(
            event="write_policy_configured",
            role="SYS",
            prod_tcp=args.prod_tcp_writes == "enabled",
            dev_tcp=args.dev_tcp_writes == "enabled",
            shine=args.shine_policy == "enabled",
            all_fc06_fc10_registers=True,
        )
        gateway.start(background=True)
        if args.mode == "cache+shine":
            virtual_adapter = ShineVirtualInverterAdapter(
                gateway.coordinator,
                discovery_profiles=(min_6000tl_xh_discovery_profile(),),
                request_handler=lambda frame, now: gateway.handle_request(
                    frame,
                    client="SHINE",
                    source="SHINE",
                    now=now,
                ),
            )
    if args.shine:
        shine = ShineEndpoint(
            args.shine,
            sh_baud,
            sh_bytes,
            ds,
            events=events,
            write_policy=write_policy,
            virtual_adapter=virtual_adapter,
        )
        shine.start()

    tcp_specs = []
    servers = []
    if args.tcp and args.tcp not in {"", "-"}:
        tcp_specs.append(args.tcp)
    if args.tcp_alt and args.tcp_alt not in {"", "-"}:
        tcp_specs.append(args.tcp_alt)

    tcp_desc = []
    for index, spec in enumerate(tcp_specs):
        try:
            host, port = parse_host_port(spec)
        except ValueError as exc:
            ap.error(str(exc))
        server = TCPServer(
            host,
            port,
            ds,
            events=events,
            source="PROD_TCP" if index == 0 else "DEV_TCP",
            gateway=gateway,
        )
        server.start()
        servers.append(server)
        tcp_desc.append(f"{host}:{port}")

    if not servers:
        ap.error("at least one TCP server must be configured (set --tcp or --tcp-alt)")

    parts = [
        f"INV={args.inverter}@{inv_baud}/{inv_bytes}",
        f"SHINE={args.shine}@{sh_baud}/{sh_bytes}",
        f"MODE={args.mode}",
        f"OPERATION={args.operation_mode}",
        f"TCP={','.join(tcp_desc)}",
    ]
    if args.config:
        parts.append(f"CONFIG={args.config}")
    if sniff_desc:
        parts.append(f"SNIFF={sniff_desc}")
    if file_logger.enabled():
        parts.append(f"LOG={file_logger.path}")
    else:
        parts.append("LOG=disabled")
    print("Broker up. " + "  ".join(parts))

    setup_export_requested = threading.Event()

    def request_setup_export(_signum, _frame) -> None:
        setup_export_requested.set()

    if setup_observer is not None and hasattr(signal, "SIGUSR2"):
        signal.signal(signal.SIGUSR2, request_setup_export)

    while True:
        if setup_export_requested.is_set():
            setup_export_requested.clear()
            if setup_observer is not None:
                export_path = args.setup_export or f"{args.config}.candidate.json"
                candidate = setup_observer.export(export_path)
                events.emit(
                    event="setup_config_exported",
                    role="SYS",
                    path=export_path,
                    blocks=len(candidate.poll_plan),
                )
                print(f"Setup candidate exported to {export_path}")
        time.sleep(0.5)


if __name__ == "__main__":
    main()
