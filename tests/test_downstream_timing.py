from __future__ import annotations

from growatt_broker.broker import (
    Downstream,
    EventHub,
    EventSink,
    RTUFramer,
    add_crc,
)


class CapturedEvents(EventSink):
    def __init__(self) -> None:
        self.events: list[dict] = []

    def handle(self, event: dict) -> None:
        self.events.append(event)


class RespondingSerial:
    def __init__(self, response: bytes, initial_data: bytes) -> None:
        self._buffer = bytearray(initial_data)
        self._response = response
        self.is_open = True

    @property
    def in_waiting(self) -> int:
        return len(self._buffer)

    def read(self, count: int) -> bytes:
        data = bytes(self._buffer[:count])
        del self._buffer[:count]
        return data

    def write(self, _data: bytes) -> int:
        self._buffer.extend(self._response)
        return len(_data)

    def flush(self) -> None:
        pass


def test_downstream_timing_separates_pre_request_and_response_scans() -> None:
    request = add_crc(bytes.fromhex("014100000001"))
    response = add_crc(bytes.fromhex("01410002"))
    serial = RespondingSerial(response, bytes(16))
    events = CapturedEvents()
    downstream = Downstream.__new__(Downstream)
    downstream.ser = serial
    downstream.framer = RTUFramer(serial, char_time=0.001)
    downstream._ensure_serial = lambda: True
    downstream._report_async_frame = lambda _request, _frame: None
    downstream.rtimeout = 0.1
    downstream._consecutive_timeouts = 0
    downstream._reopen_after_timeouts = 5
    downstream.events = EventHub([events])

    actual = downstream._transact_physical(
        request,
        client="test-client",
        source="DEV_TCP",
        standard_modbus=False,
    )

    assert actual == response
    timing = next(
        event for event in events.events if event.get("event") == "downstream_timing"
    )
    assert timing["pre_request_scan_count"] == 1
    assert timing["pre_request_scan_input_bytes"] == 16
    assert timing["pre_request_scan_max_input_bytes"] == 16
    assert timing["pre_request_scan_window_bytes"] == 16
    assert timing["pre_request_scan_max_window_bytes"] == 16
    assert timing["response_scan_count"] == 1
    assert timing["response_scan_input_bytes"] == 16 + len(response)
    assert timing["response_scan_max_input_bytes"] == 16 + len(response)
    assert timing["response_scan_window_bytes"] == 16 + len(response)
    assert timing["response_scan_max_window_bytes"] == 16 + len(response)
    assert timing["pre_request_scan_max_ms"] <= timing["pre_request_scan_ms"]
    assert timing["response_scan_max_ms"] <= timing["response_scan_ms"]
    assert timing["other_scan_count"] == 0
    assert timing["crc_scan_count"] == 2
    assert timing["crc_scan_input_bytes"] == 32 + len(response)
    assert timing["crc_scan_window_bytes"] == 32 + len(response)
    phase_scan_total = (
        timing["pre_request_scan_ms"]
        + timing["response_scan_ms"]
        + timing["other_scan_ms"]
    )
    assert abs(timing["frame_scan_ms"] - phase_scan_total) <= 0.002
