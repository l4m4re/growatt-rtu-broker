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
import argparse, socket, threading, time, json, datetime
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
    return body + c.to_bytes(2, 'little')


def crc_ok(frame: bytes) -> bool:
    if len(frame) < 4:
        return False
    return modbus_crc(frame[:-2]) == int.from_bytes(frame[-2:], 'little')


class RTUFramer:
    def __init__(self, ser: serial.Serial, char_time: float, gap_chars: float = 3.5):
        self.ser = ser
        self.char_time = char_time
        self.gap = gap_chars * char_time
        self.buf = bytearray()
        self.last = time.perf_counter()

    def read_frame(self, timeout: float = 3.0) -> bytes:
        start = time.perf_counter()
        while True:
            n = self.ser.in_waiting
            now = time.perf_counter()
            if n:
                self.buf.extend(self.ser.read(n))
                self.last = now
            else:
                if self.buf and (now - self.last) >= self.gap:
                    frame = bytes(self.buf)
                    self.buf.clear()
                    return frame
                if (now - start) > timeout:
                    if self.buf:
                        frame = bytes(self.buf)
                        self.buf.clear()
                        return frame
                    return b""
                time.sleep(self.char_time * 0.5)


def now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="milliseconds")


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


class WireLogger:
    def __init__(self, path: str = "/var/log/growatt_broker.jsonl"):
        self.path = path
        self._lock = threading.Lock()
        # Ensure directory and file exist so tail -f works before first event
        try:
            import os
            d = os.path.dirname(self.path) or "."
            os.makedirs(d, exist_ok=True)
            with open(self.path, "a", encoding="utf-8"):
                pass
        except Exception:
            # Non-fatal: logging will attempt file creation on first write
            pass

    def log(self, **event):
        event["ts"] = now_iso()
        line = json.dumps(event, ensure_ascii=False)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")


class Downstream:
    def __init__(self, dev: str, baud: int, fmt: str, *, min_cmd_period: float = 1.0, rtimeout: float = 1.5, logger: WireLogger | None = None):
        databits = int(fmt[0]); parity = fmt[1].upper(); stop = int(fmt[2])
        py_par = {'N': serial.PARITY_NONE, 'E': serial.PARITY_EVEN, 'O': serial.PARITY_ODD}[parity]
        py_stp = {1: serial.STOPBITS_ONE, 2: serial.STOPBITS_TWO}[stop]
        self.ser = serial.Serial(dev, baud, bytesize=databits, parity=py_par, stopbits=py_stp, timeout=0)
        bits_per_char = 1 + databits + stop + (0 if parity == 'N' else 1)
        self.char_time = bits_per_char / baud
        self.framer = RTUFramer(self.ser, self.char_time)
        self.lock = threading.Lock()
        self.min_cmd_period = float(min_cmd_period)
        self.rtimeout = float(rtimeout)
        self._last_done = 0.0
        self.logger = logger

    def _enforce_spacing(self):
        now = time.perf_counter()
        wait = self.min_cmd_period - (now - self._last_done)
        if wait > 0:
            time.sleep(wait)

    def transact(self, req: bytes, *, client: str = "UNKNOWN") -> bytes:
        with self.lock:
            self._enforce_spacing()
            _ = self.ser.read(self.ser.in_waiting or 0)
            if self.logger:
                self.logger.log(role="REQ", from_client=client, crc_ok=crc_ok(req), hex=req.hex(), **parse_rtu(req))
            self.ser.write(req); self.ser.flush()
            resp = self.framer.read_frame(timeout=self.rtimeout)
            self._last_done = time.perf_counter()
            if self.logger:
                self.logger.log(role="RSP", to_client=client, crc_ok=crc_ok(resp), hex=(resp.hex() if resp else ""), **parse_rtu(resp or b""))
            return resp


class ShineEndpoint(threading.Thread):
    def __init__(self, dev: str, baud: int, fmt: str, downstream: Downstream):
        super().__init__(daemon=True)
        databits = int(fmt[0]); parity = fmt[1].upper(); stop = int(fmt[2])
        py_par = {'N': serial.PARITY_NONE, 'E': serial.PARITY_EVEN, 'O': serial.PARITY_ODD}[parity]
        py_stp = {1: serial.STOPBITS_ONE, 2: serial.STOPBITS_TWO}[stop]
        self.ser = serial.Serial(dev, baud, bytesize=databits, parity=py_par, stopbits=py_stp, timeout=0)
        bits_per_char = 1 + databits + stop + (0 if parity == 'N' else 1)
        self.framer = RTUFramer(self.ser, bits_per_char / baud)
        self.ds = downstream

    def run(self):
        while True:
            req = self.framer.read_frame(timeout=10.0)
            if not req:
                continue
            if len(req) >= 4 and crc_ok(req):
                resp = self.ds.transact(req, client="SHINE")
                if resp:
                    self.ser.write(resp); self.ser.flush()


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
                tid = hdr[0:2]; pid = hdr[2:4]; length = int.from_bytes(hdr[4:6], 'big'); uid = hdr[6]
                pdu = self._recv_exact(conn, length - 1)
                if not pdu:
                    break
                rtu_req = add_crc(bytes([uid]) + pdu)
                rtu_resp = self.ds.transact(rtu_req, client=peer)
                if not rtu_resp or len(rtu_resp) < 4 or not crc_ok(rtu_resp):
                    break
                uid2 = rtu_resp[0]; pdu2 = rtu_resp[1:-2]
                rsp_len = len(pdu2) + 1
                mbap = tid + pid + rsp_len.to_bytes(2, 'big') + bytes([uid2])
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
    ap = argparse.ArgumentParser(description="Growatt broker: Shine serial + Modbus-TCP -> single RTU master")
    ap.add_argument("--inverter", required=True, help="Downstream RS-485 serial device (to inverter)")
    ap.add_argument("--shine", required=True, help="Upstream ShineWiFi-X serial device")
    ap.add_argument("--inv-baud", type=int, help="Inverter baudrate")
    ap.add_argument("--inv-bytes", default=None, help="Inverter format, e.g. 8E1")
    ap.add_argument("--shine-baud", type=int, help="Shine baudrate")
    ap.add_argument("--shine-bytes", default=None, help="Shine format, e.g. 8E1")
    ap.add_argument("--baud", type=int, default=9600, help="Default baud if side-specific not set")
    ap.add_argument("--bytes", default="8E1", help="Default serial format if side-specific not set")
    ap.add_argument("--tcp", default="0.0.0.0:5020", help="Bind host:port for Modbus-TCP server")
    ap.add_argument("--min-period", type=float, default=1.0, help="Min seconds between transactions")
    ap.add_argument("--rtimeout", type=float, default=1.5, help="RTU read timeout seconds")
    ap.add_argument("--log", default="/var/log/growatt_broker.jsonl", help="JSONL log path")
    args = ap.parse_args()

    inv_baud = args.inv_baud or args.baud
    inv_bytes = args.inv_bytes or args.bytes
    sh_baud = args.shine_baud or args.baud
    sh_bytes = args.shine_bytes or args.bytes

    host, port = args.tcp.split(":"); port = int(port)
    logger = WireLogger(args.log)
    ds = Downstream(args.inverter, inv_baud, inv_bytes, min_cmd_period=args.min_period, rtimeout=args.rtimeout, logger=logger)
    ShineEndpoint(args.shine, sh_baud, sh_bytes, ds).start()
    TCPServer(host, port, ds).start()
    print(f"Broker up. INV={args.inverter}@{inv_baud}/{inv_bytes}  SHINE={args.shine}@{sh_baud}/{sh_bytes}  TCP={host}:{port}")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
