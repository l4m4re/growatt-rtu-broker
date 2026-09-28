from __future__ import annotations

import threading

from growatt_broker.broker import (
    Downstream,
    DownstreamRequest,
)


def test_scheduler_serves_demand_reads_before_background() -> None:
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

    assert scheduler._select_request().source == "PROD_TCP"


def test_scheduler_serves_write_sources_in_priority_order() -> None:
    scheduler = Downstream.__new__(Downstream)
    scheduler._pending = [
        DownstreamRequest(
            request=b"shine-write",
            client="SHINE",
            source="SHINE",
            standard_modbus=True,
            queued_ns=1,
            done=threading.Event(),
            is_write=True,
        ),
        DownstreamRequest(
            request=b"dev-write",
            client="TCP:dev",
            source="DEV_TCP",
            standard_modbus=True,
            queued_ns=2,
            done=threading.Event(),
            is_write=True,
        ),
        DownstreamRequest(
            request=b"prod-write",
            client="TCP:prod",
            source="PROD_TCP",
            standard_modbus=True,
            queued_ns=3,
            done=threading.Event(),
            is_write=True,
        ),
    ]

    assert scheduler._select_request().source == "PROD_TCP"
    assert scheduler._select_request().source == "DEV_TCP"
    assert scheduler._select_request().source == "SHINE"



def test_write_priority_precedes_background_refresh() -> None:
    scheduler = Downstream.__new__(Downstream)
    scheduler._pending = [
        DownstreamRequest(
            request=b"background",
            client="PREFETCH",
            source="BACKGROUND",
            standard_modbus=True,
            queued_ns=1,
            done=threading.Event(),
        ),
        DownstreamRequest(
            request=b"write",
            client="TCP:dev",
            source="DEV_TCP",
            standard_modbus=True,
            queued_ns=2,
            done=threading.Event(),
            is_write=True,
        ),
    ]

    assert scheduler._select_request().is_write is True
