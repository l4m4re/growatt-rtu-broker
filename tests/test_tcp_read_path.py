"""Regression tests for Modbus-TCP reads and the shared register cache."""

from __future__ import annotations

import socket
import threading
import time

import pytest
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.framer import FramerType

from growatt_broker.broker import CacheGatewayService, EventHub, TCPServer, add_crc
from growatt_broker.configuration import load_installation_config


OLD_POLICIES = load_installation_config(
    __file__.replace(
        "tests/test_tcp_read_path.py",
        "configs/examples/growatt-min6000tl-xh-shinewifi-x-old.json",
    )
).policies()


class FakeDownstream:
    """Return one deterministic single-register response per transaction."""

    def __init__(self, delay: float = 0.0) -> None:
        self.calls = 0
        self.delay = delay
        self.requests: list[bytes] = []

    def transact(self, request: bytes, **_: object) -> bytes:
        self.calls += 1
        self.requests.append(request)
        if self.delay:
            time.sleep(self.delay)
        return add_crc(bytes([1, request[1], 2, 0, self.calls]))


class FakeBlockDownstream(FakeDownstream):
    """Return a complete response for every requested register block."""

    def transact(self, request: bytes, **_: object) -> bytes:
        self.calls += 1
        self.requests.append(request)
        if self.delay:
            time.sleep(self.delay)
        count = int.from_bytes(request[4:6], "big")
        words = (self.calls,) * count
        return add_crc(
            bytes([request[0], request[1], count * 2])
            + b"".join(word.to_bytes(2, "big") for word in words)
        )


class EventCollector:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def handle(self, event: dict) -> None:
        self.events.append(event)


def _request(tid: int, start: int, count: int = 1) -> bytes:
    pdu = bytes([4]) + start.to_bytes(2, "big") + count.to_bytes(2, "big")
    return (
        tid.to_bytes(2, "big")
        + b"\x00\x00"
        + (len(pdu) + 1).to_bytes(2, "big")
        + bytes([1])
        + pdu
    )


def _response(sock: socket.socket) -> tuple[int, bytes]:
    header = sock.recv(7)
    assert len(header) == 7
    length = int.from_bytes(header[4:6], "big")
    body = sock.recv(length - 1)
    assert len(body) == length - 1
    return int.from_bytes(header[:2], "big"), body


def _connection(server: TCPServer) -> tuple[socket.socket, threading.Thread]:
    server_side, client_side = socket.socketpair()
    thread = threading.Thread(target=server.handle, args=(server_side,))
    thread.start()
    return client_side, thread


def test_duplicate_reads_are_forwarded_in_legacy_mode() -> None:
    downstream = FakeDownstream(delay=0.05)
    collector = EventCollector()
    events = EventHub([collector])
    server = TCPServer("127.0.0.1", 0, downstream, events=events)  # type: ignore[arg-type]
    client_side, thread = _connection(server)

    try:
        client_side.sendall(_request(4, 3125))
        time.sleep(0.01)
        client_side.sendall(_request(4, 3125) + _request(5, 3000))

        first_tid, _ = _response(client_side)
        duplicate_tid, _ = _response(client_side)
        next_tid, _ = _response(client_side)

        assert (first_tid, duplicate_tid, next_tid) == (4, 4, 5)
        assert downstream.calls == 3
        assert not collector.events
    finally:
        client_side.close()
        thread.join(timeout=1)
        server.sock.close()


def test_same_pdu_with_different_tid_is_independent() -> None:
    downstream = FakeDownstream()
    server = TCPServer("127.0.0.1", 0, downstream)  # type: ignore[arg-type]
    client_side, thread = _connection(server)

    try:
        client_side.sendall(_request(6, 3000) + _request(7, 3000))
        first = _response(client_side)[0]
        second = _response(client_side)[0]
        assert (first, second) == (6, 7)
        assert downstream.calls == 2
    finally:
        client_side.close()
        thread.join(timeout=1)
        server.sock.close()


def test_same_tid_with_different_pdu_is_independent() -> None:
    downstream = FakeDownstream()
    server = TCPServer("127.0.0.1", 0, downstream)  # type: ignore[arg-type]
    client_side, thread = _connection(server)

    try:
        client_side.sendall(_request(8, 3000) + _request(8, 3125))
        first = _response(client_side)[0]
        second = _response(client_side)[0]
        assert (first, second) == (8, 8)
        assert downstream.calls == 2
    finally:
        client_side.close()
        thread.join(timeout=1)
        server.sock.close()


def test_physical_read_exception_keeps_tcp_connection_usable() -> None:
    class ExceptionDownstream:
        def __init__(self) -> None:
            self.calls = 0

        def transact(self, request: bytes, **_: object) -> bytes:
            self.calls += 1
            return add_crc(bytes([request[0], request[1] | 0x80, 0x01]))

    downstream = ExceptionDownstream()
    server = TCPServer(
        "127.0.0.1",
        0,
        downstream,  # type: ignore[arg-type]
        gateway=CacheGatewayService(downstream),  # type: ignore[arg-type]
    )
    client_side, thread = _connection(server)

    try:
        client_side.sendall(_request(10, 3000) + _request(10, 3000))
        first_tid, first_body = _response(client_side)
        second_tid, second_body = _response(client_side)

        assert (first_tid, second_tid) == (10, 10)
        assert first_body == second_body == bytes.fromhex("8401")
        assert downstream.calls == 2
    finally:
        client_side.close()
        thread.join(timeout=1)
        server.sock.close()


def test_duplicate_reads_are_served_from_the_shared_register_cache() -> None:
    downstream = FakeBlockDownstream()
    gateway = CacheGatewayService(downstream, policies=OLD_POLICIES)
    server = TCPServer(
        "127.0.0.1",
        0,
        downstream,  # type: ignore[arg-type]
        gateway=gateway,
    )
    client_side, thread = _connection(server)

    try:
        client_side.sendall(_request(9, 3000) + _request(9, 3000))
        first_tid, first_body = _response(client_side)
        second_tid, second_body = _response(client_side)
        assert (first_tid, second_tid) == (9, 9)
        assert first_body == second_body
        assert downstream.calls == 1
        assert downstream.requests == [add_crc(bytes.fromhex("01040bb8007d"))]
    finally:
        client_side.close()
        thread.join(timeout=1)
        server.sock.close()


@pytest.mark.asyncio
async def test_real_pymodbus_retry_reuses_register_cache() -> None:
    """Exercise the actual PyModbus retry path, not a handcrafted retry only."""
    downstream = FakeBlockDownstream(delay=0.15)
    gateway = CacheGatewayService(downstream, policies=())
    server = TCPServer(
        "127.0.0.1",
        0,
        downstream,  # type: ignore[arg-type]
        gateway=gateway,
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    thread = threading.Thread(target=lambda: server.handle(listener.accept()[0]))
    thread.start()
    client = AsyncModbusTcpClient(
        "127.0.0.1",
        port=listener.getsockname()[1],
        framer=FramerType.SOCKET,
        timeout=0.1,
        retries=1,
        reconnect_delay=0,
    )

    try:
        await client.connect()
        result = await client.read_input_registers(3000, count=1, device_id=1)
        assert not result.isError()
        assert downstream.calls == 1
    finally:
        client.close()
        listener.close()
        thread.join(timeout=1)
        server.sock.close()
