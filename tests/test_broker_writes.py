from __future__ import annotations

import socket
import threading
import time

from growatt_broker.broker import (
    CacheGatewayService,
    TCPServer,
    WritePolicy,
    add_crc,
)
from growatt_broker.cache_gateway import RegisterKey
from growatt_broker.configuration import load_installation_config


OLD_CONFIG = load_installation_config(
    __file__.replace(
        "tests/test_broker_writes.py",
        "configs/examples/growatt-min6000tl-xh-shinewifi-x.json",
    )
)


def _gateway(downstream: object, **kwargs: object) -> CacheGatewayService:
    return CacheGatewayService(downstream, policies=OLD_CONFIG.policies(), **kwargs)  # type: ignore[arg-type]


class WriteDownstream:
    def __init__(self, *, fail_write: bytes | None = None) -> None:
        self.requests: list[bytes] = []
        self.kwargs: list[dict[str, object]] = []
        self.fail_write = fail_write

    def transact(self, request: bytes, **kwargs: object) -> bytes:
        self.requests.append(request)
        self.kwargs.append(kwargs)
        if request[1] in (0x06, 0x10):
            if request == self.fail_write:
                return b""
            if request[1] == 0x06:
                return request
            return add_crc(request[:6])
        count = int.from_bytes(request[4:6], "big")
        return add_crc(bytes([request[0], request[1], count * 2]) + b"\x00\x01" * count)


def test_fc06_is_physically_written_without_readback() -> None:
    downstream = WriteDownstream()
    gateway = _gateway(downstream)
    gateway.cache.put_block(
        RegisterKey(3, 180, 20),
        range(20),
        captured_at=1.0,
        source_transaction="before-write",
    )
    request = add_crc(bytes.fromhex("010600bc0001"))

    result = gateway.handle_write_request(request, client="TCP:dev", source="DEV_TCP")

    assert result.status == "served"
    assert result.reason == "physical_write_acknowledged"
    assert downstream.requests == [request]
    assert downstream.kwargs[0]["is_write"] is True
    assert gateway.cache.read(
        RegisterKey(3, 188, 1), now=time.monotonic(), max_age=5
    ) is None
    read_result = gateway.handle_standard_request(
        add_crc(bytes.fromhex("010300bc0001")),
        client="TCP:dev",
        source="DEV_TCP",
    )
    assert read_result.status == "served"
    assert read_result.read is not None
    assert read_result.read.words == (1,)
    assert len(downstream.requests) == 2


def test_write_readback_rearms_affected_block_after_refresh() -> None:
    downstream = WriteDownstream()
    gateway = _gateway(downstream)
    key = RegisterKey(3, 180, 20)
    request = add_crc(bytes.fromhex("010600bc0001"))

    gateway.handle_write_request(request, client="TCP:dev", source="DEV_TCP")

    assert gateway._next_due[key] <= time.monotonic()


def test_write_invalidates_cache_before_physical_transaction() -> None:
    request = add_crc(bytes.fromhex("010600bc0001"))

    class InspectingDownstream(WriteDownstream):
        def transact(self, request: bytes, **kwargs: object) -> bytes:
            if request[1] == 0x06:
                assert (
                    gateway.cache.read(
                        RegisterKey(3, 188, 1),
                        now=time.monotonic(),
                        max_age=5,
                    )
                    is None
                )
            return super().transact(request, **kwargs)

    downstream = InspectingDownstream()
    gateway = _gateway(downstream)
    gateway.cache.put_block(
        RegisterKey(3, 180, 20),
        range(20),
        captured_at=time.monotonic(),
        source_transaction="before-write",
    )

    result = gateway.handle_write_request(request, client="TCP:dev", source="DEV_TCP")

    assert result.status == "served"


def test_fc10_preserves_values_without_readback() -> None:
    downstream = WriteDownstream()
    gateway = _gateway(downstream)
    request = add_crc(bytes.fromhex("01100be000020400010002"))

    result = gateway.handle_write_request(request, client="TCP:prod", source="PROD_TCP")

    assert result.status == "served"
    assert downstream.requests[0] == request
    assert downstream.kwargs[0]["source"] == "PROD_TCP"
    assert len(downstream.requests) == 1


def test_write_policy_allows_unknown_holding_range_when_enabled() -> None:
    downstream = WriteDownstream()
    gateway = _gateway(downstream)
    request = add_crc(bytes.fromhex("010600c80001"))

    result = gateway.handle_write_request(request, client="TCP:dev", source="DEV_TCP")

    assert result.status == "served"
    assert downstream.requests[0] == request


def test_write_policy_can_disable_each_tcp_source() -> None:
    request = add_crc(bytes.fromhex("010600c80001"))
    for source in ("PROD_TCP", "DEV_TCP"):
        downstream = WriteDownstream()
        policy = WritePolicy(
            prod_tcp_enabled=source != "PROD_TCP",
            dev_tcp_enabled=source != "DEV_TCP",
        )
        gateway = _gateway(downstream, write_policy=policy)

        result = gateway.handle_write_request(request, client="TCP", source=source)

        assert result.status == "failed"
        assert result.reason == "write_denied"
        assert result.response == add_crc(bytes.fromhex("018602"))
        assert downstream.requests == []


def test_write_policy_can_disable_shine_source() -> None:
    request = add_crc(bytes.fromhex("010600c80001"))
    downstream = WriteDownstream()
    gateway = _gateway(
        downstream,
        write_policy=WritePolicy(shine_enabled=False),
    )

    result = gateway.handle_request(request, client="SHINE", source="SHINE")

    assert result.status == "failed"
    assert result.reason == "write_denied"
    assert result.response == add_crc(bytes.fromhex("018602"))
    assert downstream.requests == []


def test_write_timeout_returns_gateway_exception_without_fake_success() -> None:
    request = add_crc(bytes.fromhex("010600bc0001"))
    downstream = WriteDownstream(fail_write=request)
    gateway = _gateway(downstream)

    result = gateway.handle_write_request(request, client="TCP:dev", source="DEV_TCP")

    assert result.status == "failed"
    assert result.reason == "physical_timeout"
    assert result.response == add_crc(bytes.fromhex("01860b"))
    assert downstream.requests == [request]


def test_physical_write_exception_is_propagated() -> None:
    request = add_crc(bytes.fromhex("010600bc0001"))

    class ExceptionDownstream(WriteDownstream):
        def transact(self, request: bytes, **kwargs: object) -> bytes:
            self.requests.append(request)
            self.kwargs.append(kwargs)
            if request[1] == 0x06:
                return add_crc(bytes.fromhex("018602"))
            return super().transact(request, **kwargs)

    downstream = ExceptionDownstream()
    result = _gateway(downstream).handle_write_request(
        request, client="TCP:dev", source="DEV_TCP"
    )

    assert result.status == "failed"
    assert result.reason == "physical_exception"
    assert result.response == add_crc(bytes.fromhex("018602"))
    assert downstream.requests[0] == request
    assert len(downstream.requests) == 1


def test_physical_read_exception_is_returned_and_not_cached() -> None:
    class ReadExceptionDownstream(WriteDownstream):
        def transact(self, request: bytes, **kwargs: object) -> bytes:
            self.requests.append(request)
            self.kwargs.append(kwargs)
            return add_crc(bytes([request[0], request[1] | 0x80, 0x01]))

    downstream = ReadExceptionDownstream()
    gateway = _gateway(downstream)
    request = add_crc(bytes.fromhex("010300bc0001"))

    first = gateway.handle_standard_request(request, client="TCP:dev", source="DEV_TCP")
    second = gateway.handle_standard_request(
        request, client="TCP:dev", source="DEV_TCP"
    )

    expected = add_crc(bytes.fromhex("018301"))
    assert first.status == second.status == "failed"
    assert first.reason == second.reason == "physical_exception"
    assert first.response == second.response == expected
    assert gateway.cache.snapshots() == ()
    native_request = add_crc(bytes.fromhex("010300b40014"))
    assert downstream.requests == [native_request, native_request]


def test_lost_write_ack_reconciles_cache_but_stays_failed() -> None:
    request = add_crc(bytes.fromhex("010600bc0001"))

    class LostAckDownstream(WriteDownstream):
        def transact(self, request: bytes, **kwargs: object) -> bytes:
            self.requests.append(request)
            self.kwargs.append(kwargs)
            if request[1] == 0x06:
                return b""
            return super().transact(request, **kwargs)

    downstream = LostAckDownstream()
    gateway = _gateway(downstream)
    gateway.cache.put_block(
        RegisterKey(3, 180, 20),
        range(20),
        captured_at=time.monotonic(),
        source_transaction="before-write",
    )

    result = gateway.handle_write_request(request, client="TCP:dev", source="DEV_TCP")

    assert result.status == "failed"
    assert result.reason == "physical_timeout"
    assert downstream.requests[0] == request
    assert len(downstream.requests) == 1
    refreshed = gateway.cache.read(
        RegisterKey(3, 188, 1), now=time.monotonic(), max_age=5
    )
    assert refreshed is None


def test_physical_write_exception_reconciles_cache() -> None:
    request = add_crc(bytes.fromhex("010600bc0001"))

    class RejectingDownstream(WriteDownstream):
        def transact(self, request: bytes, **kwargs: object) -> bytes:
            self.requests.append(request)
            self.kwargs.append(kwargs)
            if request[1] == 0x06:
                return add_crc(bytes.fromhex("018602"))
            return super().transact(request, **kwargs)

    downstream = RejectingDownstream()
    gateway = _gateway(downstream)
    gateway.cache.put_block(
        RegisterKey(3, 180, 20),
        range(20),
        captured_at=time.monotonic(),
        source_transaction="before-write",
    )

    result = gateway.handle_write_request(request, client="TCP:dev", source="DEV_TCP")

    assert result.status == "failed"
    assert result.reason == "physical_exception"
    assert result.response == add_crc(bytes.fromhex("018602"))
    assert len(downstream.requests) == 1
    assert gateway.cache.read(
        RegisterKey(3, 188, 1), now=time.monotonic(), max_age=5
    ) is None


def test_fc10_reconciles_all_overlapping_native_blocks() -> None:
    downstream = WriteDownstream()
    gateway = _gateway(downstream)
    for start, count in ((180, 20), (209, 15)):
        gateway.cache.put_block(
            RegisterKey(3, start, count),
            range(count),
            captured_at=time.monotonic(),
            source_transaction="before-write",
        )
    values = b"".join(value.to_bytes(2, "big") for value in range(30))
    request = add_crc(bytes.fromhex("011000be001e3c") + values)

    result = gateway.handle_write_request(request, client="TCP:dev", source="DEV_TCP")

    assert result.status == "served"
    assert downstream.requests == [request]
    assert gateway.cache.read(
        RegisterKey(3, 180, 20), now=time.monotonic(), max_age=5
    ) is None
    assert gateway.cache.read(
        RegisterKey(3, 209, 15), now=time.monotonic(), max_age=5
    ) is None


def test_invalid_physical_write_response_returns_gateway_exception() -> None:
    request = add_crc(bytes.fromhex("010600bc0001"))

    class InvalidResponseDownstream(WriteDownstream):
        def transact(self, request: bytes, **kwargs: object) -> bytes:
            self.requests.append(request)
            self.kwargs.append(kwargs)
            return b"invalid"

    result = _gateway(InvalidResponseDownstream()).handle_write_request(
        request, client="TCP:dev", source="DEV_TCP"
    )

    assert result.status == "failed"
    assert result.reason == "invalid_physical_write_response"
    assert result.response == add_crc(bytes.fromhex("01860b"))


def test_write_acknowledgement_leaves_holding_cache_invalidated() -> None:
    request = add_crc(bytes.fromhex("010600bc0001"))

    class ReadbackFailureDownstream(WriteDownstream):
        def transact(self, request: bytes, **kwargs: object) -> bytes:
            self.requests.append(request)
            self.kwargs.append(kwargs)
            return request

    downstream = ReadbackFailureDownstream()
    gateway = _gateway(downstream)
    gateway.cache.put_block(
        RegisterKey(3, 180, 20),
        range(20),
        captured_at=time.monotonic(),
        source_transaction="before-write",
    )

    result = gateway.handle_write_request(request, client="TCP:dev", source="DEV_TCP")

    assert result.status == "served"
    assert result.reason == "physical_write_acknowledged"
    assert (
        gateway.cache.read(RegisterKey(3, 188, 1), now=time.monotonic(), max_age=5)
        is None
    )


def test_prod_and_dev_tcp_use_same_write_gateway_policy() -> None:
    for source in ("PROD_TCP", "DEV_TCP"):
        downstream = WriteDownstream()
        gateway = _gateway(downstream)
        request = add_crc(bytes.fromhex("010600bc0001"))

        result = gateway.handle_write_request(
            request, client=f"TCP:{source}", source=source
        )

        assert result.status == "served"
        assert downstream.kwargs[0]["source"] == source


def test_tcp_server_propagates_physical_write_response() -> None:
    downstream = WriteDownstream()
    server = TCPServer("127.0.0.1", 0, downstream, source="DEV_TCP", gateway=_gateway(downstream))  # type: ignore[arg-type]
    server_side, client_side = socket.socketpair()
    thread = threading.Thread(target=server.handle, args=(server_side,))
    thread.start()
    pdu = bytes.fromhex("0600bc0001")
    client_side.sendall(b"\x00\x07\x00\x00\x00\x06\x01" + pdu)

    try:
        header = client_side.recv(7)
        body = client_side.recv(int.from_bytes(header[4:6], "big") - 1)
        assert header[:2] == b"\x00\x07"
        assert body == pdu
        assert downstream.requests[0] == add_crc(bytes([1]) + pdu)
    finally:
        client_side.close()
        thread.join(timeout=1)
        server.sock.close()



def test_read_waits_while_write_and_readback_are_unresolved() -> None:
    request = add_crc(bytes.fromhex("010600bc0001"))
    entered = threading.Event()
    release = threading.Event()

    class BlockingDownstream(WriteDownstream):
        def transact(self, request: bytes, **kwargs: object) -> bytes:
            if request[1] == 0x06:
                entered.set()
                assert release.wait(1)
            return super().transact(request, **kwargs)

    downstream = BlockingDownstream()
    gateway = _gateway(downstream)
    gateway.cache.put_block(
        RegisterKey(3, 180, 20),
        range(20),
        captured_at=time.monotonic(),
        source_transaction="before-write",
    )
    write_result: list[object] = []
    read_result: list[object] = []
    write_thread = threading.Thread(
        target=lambda: write_result.append(
            gateway.handle_write_request(request, client="TCP", source="DEV_TCP")
        )
    )
    write_thread.start()
    assert entered.wait(1)
    read_thread = threading.Thread(
        target=lambda: read_result.append(
            gateway.handle_standard_request(
                add_crc(bytes.fromhex("010300bc0001")),
                client="TCP",
                source="DEV_TCP",
            )
        )
    )
    read_thread.start()
    time.sleep(0.05)
    assert not read_result
    release.set()
    write_thread.join(timeout=1)
    read_thread.join(timeout=1)
    assert write_result[0].reason == "physical_write_acknowledged"
    assert read_result[0].status == "served"


def test_installation_plan_uses_identity_only_and_broker_age_target() -> None:
    plan = OLD_CONFIG.policies()

    assert {policy.interval for policy in plan} == {60.0}
    assert {policy.max_age for policy in plan} == {180.0}
    assert {policy.priority for policy in plan} == {5}



def test_autonomous_poller_refreshes_without_client_requests() -> None:
    downstream = WriteDownstream()
    gateway = _gateway(downstream)
    gateway.start()
    try:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and len(downstream.requests) < 3:
            time.sleep(0.01)
        assert downstream.requests
        assert bytes.fromhex("01040bb8007d") in [
            request[:-2] for request in downstream.requests
        ]
        status = gateway.cache_status()
        assert any(block["fresh"] for block in status["blocks"])
    finally:
        gateway.stop()
