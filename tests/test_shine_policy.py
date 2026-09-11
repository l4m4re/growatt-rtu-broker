from __future__ import annotations

import threading

from growatt_broker.broker import (
    Downstream,
    DownstreamRequest,
    add_crc,
    shine_policy_disposition,
)


def _read(function: int = 0x04) -> bytes:
    return add_crc(bytes([1, function, 0, 10, 0, 1]))


def test_read_only_policy_allows_standard_reads_only() -> None:
    assert shine_policy_disposition(_read()) == "forwarded"
    assert shine_policy_disposition(_read(0x03)) == "forwarded"
    assert shine_policy_disposition(
        add_crc(bytes.fromhex("0003002b0001"))
    ) == "blocked_broadcast_read"
    assert shine_policy_disposition(add_crc(bytes.fromhex("010600100001"))) == (
        "blocked_write_single"
    )
    assert shine_policy_disposition(
        add_crc(bytes.fromhex("0110001000020400010002"))
    ) == "blocked_write_multiple"
    assert shine_policy_disposition(add_crc(bytes.fromhex("012000000001"))) == (
        "blocked_unknown_fc20"
    )


def test_transparent_policy_is_explicit() -> None:
    requests = (
        _read(),
        add_crc(bytes.fromhex("010600100001")),
        add_crc(bytes.fromhex("0110001000020400010002")),
        add_crc(bytes.fromhex("012000000001")),
        add_crc(bytes.fromhex("012100000001")),
    )

    assert all(
        shine_policy_disposition(request, "transparent") == "forwarded"
        for request in requests
    )


def test_scheduler_prefers_shine_and_then_serves_production() -> None:
    scheduler = Downstream.__new__(Downstream)
    scheduler._pending = [
        DownstreamRequest(
            request=b"shine",
            client="SHINE",
            source="SHINE",
            standard_modbus=False,
            queued_ns=2,
            done=threading.Event(),
        ),
        DownstreamRequest(
            request=b"prod",
            client="TCP:prod",
            source="PROD_TCP",
            standard_modbus=False,
            queued_ns=1,
            done=threading.Event(),
        ),
    ]
    scheduler._shine_burst = 1
    scheduler._consecutive_shine = 0

    assert scheduler._select_request().source == "SHINE"
    scheduler._consecutive_shine = 1
    assert scheduler._select_request().source == "PROD_TCP"


def test_predictive_refresh_yields_to_a_pending_shine_request() -> None:
    scheduler = Downstream.__new__(Downstream)
    scheduler._pending = [
        DownstreamRequest(
            request=b"predictive",
            client="PREFETCH",
            source="PREDICTIVE",
            standard_modbus=True,
            queued_ns=1,
            done=threading.Event(),
        ),
        DownstreamRequest(
            request=b"shine",
            client="SHINE",
            source="SHINE",
            standard_modbus=True,
            queued_ns=2,
            done=threading.Event(),
        ),
    ]
    scheduler._shine_burst = 8
    scheduler._consecutive_shine = 0

    assert scheduler._select_request().source == "SHINE"
