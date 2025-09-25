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
import argparse, socket, threading, time, json, datetime, os
from typing import Iterable, Optional, List
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


class RTUFramer:
    def __init__(self, ser: serial.Serial, char_time: float, gap_chars: float = 3.5):
        self.ser = ser
        self.char_time = char_time
        # At high baud on Linux, user-space gaps between recv bursts can be > a few ms.
        # Use 3.5 char times but never below a safe floor to avoid premature frame cuts.
        gap_floor = 0.020  # 20ms floor
        self.gap = max(gap_chars * char_time, gap_floor)
        self.buf = bytearray()
        self.last = time.perf_counter()

    def read_frame(self, timeout: float = 3.0) -> bytes:
        start = time.perf_counter()
        while True:
            n = self.ser.in_waiting
            now = time.perf_counter()
            if n:
                # Defensive: cap single-read growth to avoid runaway memory
                # usage if in_waiting reports a huge value (driver / USB blip).
                cap = min(n, 4096)
                chunk = self.ser.read(cap)
                if chunk:
                    self.buf.extend(chunk)
                self.last = now
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
                        self.buf.clear()
                    return b""
                # Don’t try to sleep sub-millisecond; use a small fixed sleep to reduce CPU
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


class Downstream:
    def __init__(
        self,
        dev: str,
        baud: int,
        fmt: str,
        *,
        min_cmd_period: float = 1.0,
        rtimeout: float = 1.5,
        events: Optional[EventHub] = None,
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
        self.framer = RTUFramer(self.ser, self.char_time)
        self.lock = threading.Lock()
        self.min_cmd_period = float(min_cmd_period)
        self.rtimeout = float(rtimeout)
        self._last_done = 0.0
        self.events = events

    def _enforce_spacing(self):
        now = time.perf_counter()
        wait = self.min_cmd_period - (now - self._last_done)
        if wait > 0:
            time.sleep(wait)

    def transact(self, req: bytes, *, client: str = "UNKNOWN") -> bytes:
        with self.lock:
            self._enforce_spacing()
            # Drain OS input buffer and clear any accumulated bytes in the
            # framer's internal buffer. If we don't clear the framer buffer
            # a previously received unsolicited frame can be returned as the
            # response to this new request, causing mis-attribution and
            # CRC/timeout confusion.
            _ = self.ser.read(self.ser.in_waiting or 0)
            try:
                self.framer.buf.clear()
                # reset last read timestamp to now so gap heuristics don't
                # treat immediately following bytes as coming before the
                # request was sent
                self.framer.last = time.perf_counter()
            except Exception:
                # be defensive: if clearing fails, continue — we prefer to
                # attempt the transaction than raise here
                pass
            if self.events:
                self.events.emit(
                    role="REQ",
                    from_client=client,
                    crc_ok=crc_ok(req),
                    hex=req.hex(),
                    **parse_rtu(req),
                )
            self.ser.write(req)
            self.ser.flush()
            resp = self.framer.read_frame(timeout=self.rtimeout)
            self._last_done = time.perf_counter()
            if not resp and self.events:
                self.events.emit(
                    event="downstream_timeout",
                    role="WARN",
                    to="INVERTER",
                    from_client=client,
                    timeout=self.rtimeout,
                )
            if self.events:
                self.events.emit(
                    role="RSP",
                    to_client=client,
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
    ):
        super().__init__(daemon=True)
        self.dev = dev
        self.baud = baud
        self.fmt = fmt
        self.ds = downstream
        self.events = events
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
                resp = self.ds.transact(req, client="SHINE")
                if resp:
                    self.ser.write(resp)
                    self.ser.flush()
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
    def __init__(self, bind_host: str, bind_port: int, downstream: Downstream):
        super().__init__(daemon=True)
        self.addr = (bind_host, bind_port)
        self.ds = downstream
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
            peer = f"TCP:{conn.getpeername()[0]}:{conn.getpeername()[1]}"
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
                rtu_req = add_crc(bytes([uid]) + pdu)
                rtu_resp = self.ds.transact(rtu_req, client=peer)
                if not rtu_resp or len(rtu_resp) < 4 or not crc_ok(rtu_resp):
                    break
                uid2 = rtu_resp[0]
                pdu2 = rtu_resp[1:-2]
                rsp_len = len(pdu2) + 1
                mbap = tid + pid + rsp_len.to_bytes(2, "big") + bytes([uid2])
                conn.sendall(mbap + pdu2)
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
        "--log",
        default="/var/log/growatt_broker.jsonl",
        help="JSONL log path (use '-' to disable)",
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

    ds = Downstream(
        args.inverter,
        inv_baud,
        inv_bytes,
        min_cmd_period=args.min_period,
        rtimeout=args.rtimeout,
        events=events,
    )
    shine = None
    if args.shine:
        shine = ShineEndpoint(args.shine, sh_baud, sh_bytes, ds, events=events)
        shine.start()

    tcp_specs = []
    if args.tcp and args.tcp not in {"", "-"}:
        tcp_specs.append(args.tcp)
    if args.tcp_alt and args.tcp_alt not in {"", "-"}:
        tcp_specs.append(args.tcp_alt)

    servers = []
    tcp_desc = []
    for spec in tcp_specs:
        try:
            host, port = parse_host_port(spec)
        except ValueError as exc:
            ap.error(str(exc))
        server = TCPServer(host, port, ds)
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
    print("Broker up. " + "  ".join(parts))

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
