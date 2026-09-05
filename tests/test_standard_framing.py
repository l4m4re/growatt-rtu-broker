import threading
import time

import pytest

from growatt_broker.broker import (
    RTUFramer,
    add_crc,
    find_standard_response,
    standard_response_spec,
)


class FakeSerial:
    def __init__(self) -> None:
        self._buffer = bytearray()
        self._lock = threading.Lock()

    @property
    def in_waiting(self) -> int:
        with self._lock:
            return len(self._buffer)

    def feed(self, data: bytes) -> None:
        with self._lock:
            self._buffer.extend(data)

    def read(self, count: int) -> bytes:
        with self._lock:
            data = bytes(self._buffer[:count])
            del self._buffer[:count]
            return data


def request(function: int, address: int, count: int) -> bytes:
    return add_crc(
        bytes([1, function])
        + address.to_bytes(2, "big")
        + count.to_bytes(2, "big")
    )


def test_fc03_partial_crc_collision_waits_for_complete_frame() -> None:
    req = request(0x03, 88, 1)
    serial = FakeSerial()
    framer = RTUFramer(serial, char_time=0.001)
    result: list[bytes] = []

    serial.feed(bytes.fromhex("010302013178"))
    thread = threading.Thread(
        target=lambda: result.append(framer.read_standard_frame(req, timeout=0.5))
    )
    thread.start()
    time.sleep(0.03)
    assert thread.is_alive()

    serial.feed(bytes.fromhex("00"))
    thread.join(timeout=0.5)

    assert not thread.is_alive()
    assert result == [bytes.fromhex("01030201317800")]


def test_fc03_short_crc_valid_prefix_is_not_a_response() -> None:
    req = request(0x03, 88, 1)
    partial = bytes.fromhex("010302013178")

    assert find_standard_response(partial, req) is None


@pytest.mark.parametrize("function", [0x03, 0x04])
@pytest.mark.parametrize("split", range(1, 9))
def test_chunked_register_response(function: int, split: int) -> None:
    req = request(function, 10, 2)
    response = add_crc(bytes([1, function, 4, 0, 1, 0, 2]))
    assert find_standard_response(response[:split], req) is None
    assert find_standard_response(response[:split] + response[split:], req) == response


def test_wrong_register_byte_count_is_not_accepted() -> None:
    req = request(0x03, 10, 2)
    response = add_crc(bytes.fromhex("0103020001"))

    assert find_standard_response(response, req) is None


def test_exception_response() -> None:
    req = request(0x03, 88, 1)
    response = add_crc(bytes.fromhex("018302"))

    assert len(response) == 5
    assert find_standard_response(response, req) == response


def test_fc06_response() -> None:
    req = add_crc(bytes.fromhex("010600100001"))
    response = add_crc(bytes.fromhex("010600100001"))

    assert standard_response_spec(req) == (1, 0x06, 8)
    assert find_standard_response(response, req) == response


def test_fc10_response() -> None:
    req = add_crc(bytes.fromhex("0110001000020400010002"))
    response = add_crc(bytes.fromhex("011000100002"))

    assert standard_response_spec(req) == (1, 0x10, 8)
    assert find_standard_response(response, req) == response


def test_nonstandard_request_has_no_deterministic_spec() -> None:
    req = add_crc(bytes.fromhex("012000000001"))

    assert standard_response_spec(req) is None


def test_nonstandard_request_uses_existing_generic_reader() -> None:
    req = add_crc(bytes.fromhex("012000000001"))
    response = add_crc(bytes.fromhex("012002"))
    serial = FakeSerial()
    serial.feed(response)
    framer = RTUFramer(serial, char_time=0.001)

    assert framer.read_standard_frame(req, timeout=0.1) == response
