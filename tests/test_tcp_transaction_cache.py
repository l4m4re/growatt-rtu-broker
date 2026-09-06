"""Regression tests for duplicate Modbus-TCP retries."""

from __future__ import annotations

import socket
import threading
import time

from growatt_broker.broker import TCPServer, add_crc


class FakeDownstream:
    """Return one deterministic response per physical transaction."""

    def __init__(self) -> None:
        self.calls = 0

    def transact(self, request: bytes, **_: object) -> bytes:
        self.calls += 1
        time.sleep(0.05)
        return add_crc(bytes([1, request[1], 2, 0, self.calls]))


def _request(tid: int, start: int) -> bytes:
    pdu = bytes([4]) + start.to_bytes(2, "big") + (1).to_bytes(2, "big")
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


def test_duplicate_retry_is_replayed_without_second_physical_transaction() -> None:
    downstream = FakeDownstream()
    server = TCPServer("127.0.0.1", 0, downstream)  # type: ignore[arg-type]
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    thread = threading.Thread(
        target=lambda: server.handle(listener.accept()[0]),
    )
    thread.start()
    client_side = socket.create_connection(listener.getsockname())

    try:
        client_side.sendall(_request(4, 3125))
        time.sleep(0.01)
        client_side.sendall(_request(4, 3125) + _request(5, 3000))

        first_tid, first_body = _response(client_side)
        replay_tid, replay_body = _response(client_side)
        next_tid, _ = _response(client_side)

        assert (first_tid, replay_tid, next_tid) == (4, 4, 5)
        assert first_body == replay_body
        assert downstream.calls == 2
    finally:
        client_side.close()
        thread.join(timeout=1)
        listener.close()
        server.sock.close()
