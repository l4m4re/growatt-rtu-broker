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
import atexit
import datetime
import json
import os
import signal
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional, List
import serial


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
    if function in (0x03, 0x04):
        if len(request) != 8:
            return None
        count = int.from_bytes(request[4:6], "big")
        return unit, function, 5 + (count * 2)
    if function in (0x06, 0x10):
        return unit, function, 8
    return None


def is_retryable_standard_read(request: bytes) -> bool:
    """Return whether a standard TCP request can be safely retried."""
    return (
        standard_response_spec(request) is not None
        and request[1] in (0x03, 0x04)
    )


def shine_policy_disposition(request: bytes, policy: str = "read-only") -> str:
    """Classify one valid Shine frame before it can reach the inverter."""
    function = request[1] if len(request) > 1 else None
    if policy == "transparent":
        return "forwarded"
    if function in (0x03, 0x04) and standard_response_spec(request) is not None:
        if request[0] == 0:
            return "blocked_broadcast_read"
        return "forwarded"
    if function == 0x06:
        return "blocked_write_single"
    if function == 0x10:
        return "blocked_write_multiple"
    if function == 0x20:
        return "blocked_unknown_fc20"
    return "blocked_unknown"


def find_standard_response(buffer: bytes, request: bytes) -> bytes | None:
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
        if buffer[start] != unit:
            continue
        if start + 5 <= len(buffer):
            candidate = buffer[start : start + 5]
            if (
                candidate[1] == (function | 0x80)
                and crc_ok(candidate)
            ):
                return candidate
        if start + normal_length > len(buffer):
            continue
        candidate = buffer[start : start + normal_length]
        if candidate[1] != function or not crc_ok(candidate):
            continue
        if function in (0x03, 0x04) and candidate[2] != (normal_length - 5):
            continue
        if function == 0x06 and candidate != request:
            continue
        if function == 0x10:
            expected = add_crc(request[0:6])
            if candidate != expected:
                continue
        return candidate
    return None


class ForensicCapture:
    """Bounded raw serial capture for an explicitly enabled forensic run."""

    def __init__(
        self,
        path: str | None,
        *,
        max_bytes: int = 10_000_000,
        max_seconds: float | None = None,
    ):
        self.path = Path(path) if path else None
        self.max_bytes = max(0, max_bytes)
        self.max_seconds = max_seconds if max_seconds and max_seconds > 0 else None
        self.started_mono = time.monotonic()
        self._captured_bytes = 0
        self._closed = False
        self._lock = threading.Lock()
        self._file = None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self.path.open("w", encoding="utf-8", buffering=1)
            atexit.register(self.close)

    @property
    def enabled(self) -> bool:
        return self._file is not None and not self._closed

    def _write(self, event: dict) -> None:
        if self._file is None or self._closed:
            return
        self._file.write(json.dumps(event, ensure_ascii=False) + "\n")

    def record_rx(
        self,
        data: bytes,
        *,
        source: str,
        captured_bytes: bool = True,
    ) -> None:
        if not data or not self.enabled:
            return
        with self._lock:
            if (
                self.max_seconds
                and time.monotonic() - self.started_mono > self.max_seconds
            ):
                return
            payload = data
            truncated = False
            if captured_bytes:
                remaining = self.max_bytes - self._captured_bytes
                if remaining <= 0:
                    return
                if len(payload) > remaining:
                    payload = payload[:remaining]
                    truncated = True
                self._captured_bytes += len(payload)
            self._write(
                {
                    "event": "physical_rx_raw",
                    "source": source,
                    "kind": "serial_read" if captured_bytes else "buffer_snapshot",
                    "captured_bytes": captured_bytes,
                    "truncated": truncated,
                    "length": len(data),
                    "captured_length": len(payload),
                    "hex": payload.hex(),
                    "ts": now_iso(),
                    "monotonic_ns": time.monotonic_ns(),
                }
            )

    def record_tx(
        self,
        data: bytes,
        *,
        client: str,
        attempt: int,
        source: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        with self._lock:
            if (
                self.max_seconds
                and time.monotonic() - self.started_mono > self.max_seconds
            ):
                return
            self._write(
                {
                    "event": "physical_tx",
                    "client": client,
                    "source": source or client,
                    "attempt": attempt,
                    "length": len(data),
                    "hex": data.hex(),
                    "ts": now_iso(),
                    "monotonic_ns": time.monotonic_ns(),
                }
            )

    def record_shine(
        self,
        data: bytes,
        *,
        direction: str,
        disposition: str,
        event: str = "shine_raw",
    ) -> None:
        """Record one bounded raw frame on the Shine serial leg."""
        if not data or not self.enabled:
            return
        with self._lock:
            if (
                self.max_seconds
                and time.monotonic() - self.started_mono > self.max_seconds
            ):
                return
            remaining = self.max_bytes - self._captured_bytes
            if remaining <= 0:
                return
            payload = data[:remaining]
            self._captured_bytes += len(payload)
            self._write(
                {
                    "event": event,
                    "source": "SHINE",
                    "direction": direction,
                    "disposition": disposition,
                    "length": len(data),
                    "captured_length": len(payload),
                    "truncated": len(payload) != len(data),
                    "hex": payload.hex(),
                    "ts": now_iso(),
                    "monotonic_ns": time.monotonic_ns(),
                }
            )

    def close(self) -> None:
        with self._lock:
            if self._file is not None and not self._closed:
                self._file.flush()
                self._file.close()
                self._closed = True


class RTUFramer:
    def __init__(
        self,
        ser: serial.Serial,
        char_time: float,
        gap_chars: float = 3.5,
        capture: Callable[[bytes, str], None] | None = None,
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

    def read_frame(self, timeout: float = 3.0) -> bytes:
        start = time.perf_counter()
        while True:
            n = self.ser.in_waiting
            now = time.perf_counter()
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
                        for start_idx in range(0, len(buf) - 3):
                            # minimal frame length is 4 bytes
                            for end_idx in range(start_idx + 4, len(buf) + 1):
                                if crc_ok(buf[start_idx:end_idx]):
                                    # Extract the first valid frame
                                    frame = buf[start_idx:end_idx]
                                    # Remove consumed bytes (including any prefix garbage)
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
                        for start_idx in range(0, len(buf) - 3):
                            for end_idx in range(start_idx + 4, len(buf) + 1):
                                if crc_ok(buf[start_idx:end_idx]):
                                    frame = buf[start_idx:end_idx]
                                    remaining = buf[end_idx:]
                                    self.buf.clear()
                                    if remaining:
                                        self.buf.extend(remaining)
                                    return frame
                    # Protect against runaway buffer growth: if buffer gets very
                    # large and no valid frame is detected, drop it and return
                    # timeout to avoid memory issues.
                    if len(self.buf) > 8192:
                        if self.capture:
                            self.capture(bytes(self.buf), "buffer_overflow_discard")
                        self.buf.clear()
                    return b""
                # Don’t sleep sub-millisecond; use a fixed sleep to reduce CPU use.
                time.sleep(max(0.001, self.char_time * 0.5))

    def read_standard_frame(self, request: bytes, timeout: float = 3.0) -> bytes:
        """Read an exact-length response for a standard Modbus request."""
        if standard_response_spec(request) is None:
            return self.read_frame(timeout=timeout)

        start = time.perf_counter()
        while True:
            if self.ser.in_waiting:
                self._read_available("normal_read")
                response = find_standard_response(bytes(self.buf), request)
                if response is not None:
                    end = self.buf.find(response) + len(response)
                    del self.buf[:end]
                    return response

            if time.perf_counter() - start > timeout:
                return b""
            time.sleep(max(0.001, self.char_time * 0.5))


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
    response: bytes = b""


class Downstream:
    """Single-owner downstream serial scheduler.

    The queue selects the next source at transaction boundaries; an active
    physical transaction is never preempted.
    """

    _SOURCE_PRIORITIES = {"SHINE": 0, "PROD_TCP": 1, "DEV_TCP": 2}

    def __init__(
        self,
        dev: str,
        baud: int,
        fmt: str,
        *,
        min_cmd_period: float = 1.0,
        rtimeout: float = 1.5,
        events: Optional[EventHub] = None,
        forensic: ForensicCapture | None = None,
        shine_burst: int = 8,
    ):
        databits = int(fmt[0])
        parity = fmt[1].upper()
        stop = int(fmt[2])
        py_par = {
            "N": serial.PARITY_NONE,
            "E": serial.PARITY_EVEN,
            "O": serial.PARITY_ODD,
        }[parity]
        py_stp = {1: serial.STOPBITS_ONE, 2: serial.STOPBITS_TWO}[stop]
        self.ser = serial.Serial(
            dev, baud, bytesize=databits, parity=py_par, stopbits=py_stp, timeout=0
        )
        bits_per_char = 1 + databits + stop + (0 if parity == "N" else 1)
        self.char_time = bits_per_char / baud
        self.forensic = forensic
        self.framer = RTUFramer(
            self.ser,
            self.char_time,
            capture=(self._capture_rx if forensic else None),
        )
        self._io_lock = threading.Lock()
        self._queue_condition = threading.Condition()
        self._pending: list[DownstreamRequest] = []
        self._shine_burst = max(1, int(shine_burst))
        self._consecutive_shine = 0
        self.min_cmd_period = float(min_cmd_period)
        self.rtimeout = float(rtimeout)
        self._last_done = 0.0
        self.events = events
        self._scheduler = threading.Thread(
            target=self._run_scheduler, name="growatt-downstream", daemon=True
        )
        self._scheduler.start()

    def _capture_rx(self, data: bytes, source: str) -> None:
        if self.forensic:
            self.forensic.record_rx(data, source=source)

    def _snapshot_buffer(self, source: str) -> None:
        if self.forensic and self.framer.buf:
            self.forensic.record_rx(
                bytes(self.framer.buf), source=source, captured_bytes=False
            )

    def final_drain(self) -> None:
        """Record bytes already available without transmitting anything."""
        with self._io_lock:
            self._snapshot_buffer("post_test_framer_buffer")
            count = self.ser.in_waiting
            if count:
                data = self.ser.read(count)
                if data:
                    self._capture_rx(data, "post_test_drain")

    def _enforce_spacing(self):
        now = time.perf_counter()
        wait = self.min_cmd_period - (now - self._last_done)
        if wait > 0:
            time.sleep(wait)

    @classmethod
    def _priority(cls, source: str, client: str) -> int:
        if source in cls._SOURCE_PRIORITIES:
            return cls._SOURCE_PRIORITIES[source]
        if client == "SHINE":
            return cls._SOURCE_PRIORITIES["SHINE"]
        return cls._SOURCE_PRIORITIES["PROD_TCP"]

    def _select_request(self) -> DownstreamRequest:
        non_shine = [item for item in self._pending if item.source != "SHINE"]
        if self._consecutive_shine >= self._shine_burst and non_shine:
            selected = min(
                non_shine,
                key=lambda item: (
                    self._priority(item.source, item.client),
                    item.queued_ns,
                ),
            )
        else:
            selected = min(
                self._pending,
                key=lambda item: (
                    self._priority(item.source, item.client),
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
            if item.source == "SHINE":
                self._consecutive_shine += 1
            else:
                self._consecutive_shine = 0
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
            finally:
                item.done.set()

    def transact(
        self,
        req: bytes,
        *,
        client: str = "UNKNOWN",
        standard_modbus: bool = False,
        source: str | None = None,
    ) -> bytes:
        source = source or ("SHINE" if client == "SHINE" else "PROD_TCP")
        item = DownstreamRequest(
            request=req,
            client=client,
            source=source,
            standard_modbus=standard_modbus,
            queued_ns=time.monotonic_ns(),
            done=threading.Event(),
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
    ) -> bytes:
        resp = b""
        attempts = 2 if standard_modbus and is_retryable_standard_read(req) else 1
        for attempt in range(attempts):
            self._enforce_spacing()
            # Discard bytes that arrived before this request so they cannot be
            # attributed to the new request.
            self._snapshot_buffer("pre_request_framer_buffer")
            drained = self.ser.read(self.ser.in_waiting or 0)
            if drained:
                self._capture_rx(drained, "pre_request_drain")
            self.framer.buf.clear()
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
            if self.forensic:
                self.forensic.record_tx(
                    req, client=client, source=source, attempt=attempt + 1
                )
            if standard_modbus:
                resp = self.framer.read_standard_frame(req, timeout=self.rtimeout)
            else:
                resp = self.framer.read_frame(timeout=self.rtimeout)
            if not resp:
                self._snapshot_buffer("timeout_residual")
            else:
                self._snapshot_buffer("leftover_framer_buffer")
            self._last_done = time.perf_counter()
            if resp or attempt + 1 == attempts:
                break
            if self.events:
                self.events.emit(
                    event="downstream_retry",
                    role="WARN",
                    to="INVERTER",
                    source=source,
                    from_client=client,
                    attempt=attempt + 2,
                )
        if not resp and self.events:
            self.events.emit(
                event="downstream_timeout",
                role="WARN",
                to="INVERTER",
                source=source,
                from_client=client,
                timeout=self.rtimeout,
            )
        if self.events:
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


class ShineEndpoint(threading.Thread):
    def __init__(
        self,
        dev: str,
        baud: int,
        fmt: str,
        downstream: Downstream,
        events: Optional[EventHub] = None,
        forensic: ForensicCapture | None = None,
        policy: str = "read-only",
    ):
        super().__init__(daemon=True)
        self.dev = dev
        self.baud = baud
        self.fmt = fmt
        self.ds = downstream
        self.events = events
        self.forensic = forensic
        self.policy = policy
        self.ser: Optional[serial.Serial] = None
        self.framer: Optional[RTUFramer] = None
        self._online = False

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
        self.ser = serial.Serial(
            self.dev,
            self.baud,
            bytesize=databits,
            parity=py_par,
            stopbits=py_stp,
            timeout=0,
        )
        bits_per_char = 1 + databits + stop + (0 if parity == "N" else 1)
        self.framer = RTUFramer(self.ser, bits_per_char / self.baud)
        self._online = True
        if self.events:
            self.events.emit(
                event="shine_online",
                role="SYS",
                port=self.dev,
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
        self._online = False
        # Logging is handled via EventHub/WireLogger; no direct stdout prints here

    def run(self):
        while True:
            if not self.ser or not self.framer:
                try:
                    self._open_port()
                except Exception as exc:
                    if self.events:
                        self.events.emit(
                            event="shine_open_failed",
                            role="WARN",
                            port=self.dev,
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
                disposition = shine_policy_disposition(req, self.policy)
                standard_request = (
                    disposition == "forwarded"
                    and standard_response_spec(req) is not None
                )
                if self.policy == "transparent":
                    # The dongle owns this serial leg; preserve the inverter's
                    # raw response shape instead of applying TCP unit matching.
                    standard_request = False

                if self.forensic:
                    self.forensic.record_shine(
                        req,
                        direction="shine_to_broker",
                        disposition=disposition,
                        event="shine_request_raw",
                    )
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

                if disposition != "forwarded":
                    resp = add_crc(bytes([req[0], function | 0x80, 0x01]))
                    if self.events:
                        self.events.emit(
                            event="shine_policy_blocked",
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
                    self.ser.write(resp)
                    self.ser.flush()
                    if self.forensic:
                        self.forensic.record_shine(
                            resp,
                            direction="broker_to_shine",
                            disposition=disposition,
                            event="shine_response_raw",
                        )
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
    _RESPONSE_CACHE_TTL = 10.0
    _RESPONSE_CACHE_MAX = 32

    def __init__(
        self,
        bind_host: str,
        bind_port: int,
        downstream: Downstream,
        events: EventHub | None = None,
        source: str = "PROD_TCP",
    ):
        super().__init__(daemon=True)
        self.addr = (bind_host, bind_port)
        self.ds = downstream
        self.events = events
        self.source = source
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(self.addr)
        self.sock.listen(8)

    def run(self):
        while True:
            conn, _ = self.sock.accept()
            threading.Thread(target=self.handle, args=(conn,), daemon=True).start()

    def handle(self, conn: socket.socket):
        response_cache: dict[bytes, tuple[float, bytes]] = {}
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
                cache_key = tid + pid + bytes([uid]) + pdu
                now = time.monotonic()
                for key, (expires, _) in list(response_cache.items()):
                    if expires <= now:
                        del response_cache[key]
                cached = response_cache.get(cache_key)
                if cached is not None:
                    if self.events:
                        self.events.emit(
                            event="tcp_duplicate_suppressed",
                            role="INFO",
                            source=self.source,
                            from_client=peer,
                            transaction_id=int.from_bytes(tid, "big"),
                            unit=uid,
                            func=pdu[0] if pdu else None,
                        )
                    continue
                rtu_req = add_crc(bytes([uid]) + pdu)
                rtu_resp = self.ds.transact(
                    rtu_req,
                    client=peer,
                    source=self.source,
                    standard_modbus=True,
                )
                if not rtu_resp or len(rtu_resp) < 4 or not crc_ok(rtu_resp):
                    break
                uid2 = rtu_resp[0]
                pdu2 = rtu_resp[1:-2]
                rsp_len = len(pdu2) + 1
                mbap = tid + pid + rsp_len.to_bytes(2, "big") + bytes([uid2])
                response = mbap + pdu2
                conn.sendall(response)
                if len(response_cache) >= self._RESPONSE_CACHE_MAX:
                    oldest = min(response_cache, key=lambda key: response_cache[key][0])
                    del response_cache[oldest]
                response_cache[cache_key] = (
                    time.monotonic() + self._RESPONSE_CACHE_TTL,
                    response,
                )
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
        required=True,
        help="Downstream RS-485 serial device (to inverter)",
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
        choices=("read-only", "transparent"),
        default="read-only",
        help="Shine forwarding policy; read-only blocks writes and unknown functions",
    )
    ap.add_argument(
        "--baud", type=int, default=9600, help="Default baud if side-specific not set"
    )
    ap.add_argument(
        "--bytes", default="8E1", help="Default serial format if side-specific not set"
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
        "--min-period", type=float, default=1.0, help="Min seconds between transactions"
    )
    ap.add_argument(
        "--rtimeout", type=float, default=1.5, help="RTU read timeout seconds"
    )
    ap.add_argument(
        "--shine-burst",
        type=int,
        default=8,
        help="Maximum consecutive Shine transactions before serving another source",
    )
    ap.add_argument(
        "--log",
        default="/var/log/growatt_broker.jsonl",
        help="JSONL log path (use '-' to disable)",
    )
    ap.add_argument(
        "--forensic-rx-log",
        default=None,
        help="Bounded inverter RX/TX forensic JSONL path (disabled by default)",
    )
    ap.add_argument(
        "--forensic-rx-max-bytes",
        type=int,
        default=10_000_000,
        help="Maximum raw RX bytes retained by forensic capture",
    )
    ap.add_argument(
        "--forensic-rx-max-seconds",
        type=float,
        default=None,
        help="Optional maximum forensic capture duration",
    )
    args = ap.parse_args()

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
    forensic = ForensicCapture(
        args.forensic_rx_log,
        max_bytes=args.forensic_rx_max_bytes,
        max_seconds=args.forensic_rx_max_seconds,
    )
    if forensic.enabled:
        events.emit(
            event="forensic_rx_enabled",
            role="SYS",
            path=str(forensic.path),
            max_bytes=forensic.max_bytes,
            max_seconds=forensic.max_seconds,
        )

    ds = Downstream(
        args.inverter,
        inv_baud,
        inv_bytes,
        min_cmd_period=args.min_period,
        rtimeout=args.rtimeout,
        events=events,
        forensic=forensic,
        shine_burst=args.shine_burst,
    )
    shine = None
    if args.shine:
        shine = ShineEndpoint(
            args.shine,
            sh_baud,
            sh_bytes,
            ds,
            events=events,
            forensic=forensic,
            policy=args.shine_policy,
        )
        shine.start()

    tcp_specs = []
    if args.tcp and args.tcp not in {"", "-"}:
        tcp_specs.append(args.tcp)
    if args.tcp_alt and args.tcp_alt not in {"", "-"}:
        tcp_specs.append(args.tcp_alt)

    servers = []
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
        )
        server.start()
        servers.append(server)
        tcp_desc.append(f"{host}:{port}")

    if not servers:
        ap.error("at least one TCP server must be configured (set --tcp or --tcp-alt)")

    parts = [
        f"INV={args.inverter}@{inv_baud}/{inv_bytes}",
        f"SHINE={args.shine}@{sh_baud}/{sh_bytes}",
        f"TCP={','.join(tcp_desc)}",
    ]
    if sniff_desc:
        parts.append(f"SNIFF={sniff_desc}")
    if file_logger.enabled():
        parts.append(f"LOG={file_logger.path}")
    else:
        parts.append("LOG=disabled")
    if forensic.enabled:
        parts.append(f"FORENSIC_RX={forensic.path}")
    print("Broker up. " + "  ".join(parts))

    final_drain_requested = threading.Event()

    def request_final_drain(_signum, _frame) -> None:
        final_drain_requested.set()

    if forensic.enabled and hasattr(signal, "SIGUSR1"):
        signal.signal(signal.SIGUSR1, request_final_drain)

    while True:
        if final_drain_requested.is_set():
            final_drain_requested.clear()
            ds.final_drain()
            events.emit(event="forensic_rx_final_drain", role="SYS")
        time.sleep(0.5)


if __name__ == "__main__":
    main()
