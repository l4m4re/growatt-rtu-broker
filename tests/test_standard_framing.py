import threading
import time

import pytest

from growatt_broker.broker import (
    RTUFramer,
    add_crc,
    find_standard_response,
    is_retryable_standard_read,
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


class BusySerial:
    def __init__(self) -> None:
        self._started = time.perf_counter()

    @property
    def in_waiting(self) -> int:
        return int(time.perf_counter() - self._started < 0.2)

    def read(self, count: int) -> bytes:
        time.sleep(0.001)
        return b"\x00" * count


def request(function: int, address: int, count: int) -> bytes:
    return add_crc(
        bytes([1, function]) + address.to_bytes(2, "big") + count.to_bytes(2, "big")
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


def test_shine_discovery_allows_inverter_unit_for_zero_unit_request() -> None:
    req = request(0x03, 43, 1).replace(b"\x01", b"\x00", 1)
    response = add_crc(bytes.fromhex("01030213ec"))
    serial = FakeSerial()
    serial.feed(response)
    framer = RTUFramer(serial, char_time=0.001)

    assert (
        framer.read_standard_frame(
            req,
            timeout=0.2,
            allow_unit_zero_wildcard=True,
        )
        == response
    )


def test_shine_standard_reader_rejects_same_function_wrong_length() -> None:
    req = request(0x03, 0, 125)
    wrong_length = add_crc(bytes([1, 0x03, 40]) + bytes(40))
    expected = add_crc(bytes([1, 0x03, 250]) + bytes(range(250)))
    serial = FakeSerial()
    serial.feed(wrong_length + expected)
    framer = RTUFramer(serial, char_time=0.001)
    unmatched: list[bytes] = []

    result = framer.read_standard_frame(
        req,
        timeout=0.2,
        on_unmatched=unmatched.append,
        allow_unit_zero_wildcard=True,
    )

    assert result == expected
    assert unmatched == [wrong_length]


def test_fc06_response() -> None:
    req = add_crc(bytes.fromhex("010600100001"))
    response = add_crc(bytes.fromhex("010600100001"))

    assert standard_response_spec(req) == (1, 0x06, 8)
    assert find_standard_response(response, req) == response


def test_fc06_same_length_wrong_register_is_rejected() -> None:
    req = add_crc(bytes.fromhex("010600100001"))
    response = add_crc(bytes.fromhex("010600110001"))

    assert find_standard_response(response, req) is None


def test_fc10_response() -> None:
    req = add_crc(bytes.fromhex("0110001000020400010002"))
    response = add_crc(bytes.fromhex("011000100002"))

    assert standard_response_spec(req) == (1, 0x10, 8)
    assert find_standard_response(response, req) == response


def test_fc10_same_length_wrong_start_or_quantity_is_rejected() -> None:
    req = add_crc(bytes.fromhex("0110001000020400010002"))
    wrong_start = add_crc(bytes.fromhex("011000110002"))
    wrong_quantity = add_crc(bytes.fromhex("011000100003"))

    assert find_standard_response(wrong_start, req) is None
    assert find_standard_response(wrong_quantity, req) is None


def test_nonstandard_request_has_no_deterministic_spec() -> None:
    req = add_crc(bytes.fromhex("012000000001"))

    assert standard_response_spec(req) is None


@pytest.mark.parametrize("function", [0x03, 0x04])
def test_standard_reads_are_retryable(function: int) -> None:
    assert is_retryable_standard_read(request(function, 73, 1))


@pytest.mark.parametrize("function", [0x06, 0x10, 0x20])
def test_writes_and_nonstandard_requests_are_not_retryable(function: int) -> None:
    req = add_crc(bytes([1, function, 0, 73, 0, 1]))

    assert not is_retryable_standard_read(req)


def test_nonstandard_request_uses_existing_generic_reader() -> None:
    req = add_crc(bytes.fromhex("012000000001"))
    response = add_crc(bytes.fromhex("012002"))
    serial = FakeSerial()
    serial.feed(response)
    framer = RTUFramer(serial, char_time=0.001)

    assert framer.read_standard_frame(req, timeout=0.1) == response


def test_matching_reader_discards_valid_unrelated_async_frame() -> None:
    async_frame = add_crc(bytes.fromhex("00090100"))
    expected = add_crc(bytes([1, 0x20, 200]) + bytes(range(200)))
    serial = FakeSerial()
    serial.feed(async_frame + expected)
    framer = RTUFramer(serial, char_time=0.001)
    discarded: list[bytes] = []

    result = framer.read_matching(
        lambda frame: frame == expected,
        timeout=0.2,
        on_unmatched=discarded.append,
    )

    assert result == expected
    assert discarded == [async_frame]


def test_matching_reader_reports_multiple_frames_from_one_serial_read() -> None:
    async_one = add_crc(bytes.fromhex("00090100"))
    async_two = add_crc(bytes.fromhex("00070100"))
    expected = add_crc(bytes.fromhex("0103020001"))
    serial = FakeSerial()
    serial.feed(async_one + async_two + expected)
    framer = RTUFramer(serial, char_time=0.001)
    discarded: list[bytes] = []

    result = framer.read_matching(
        lambda frame: frame == expected,
        timeout=0.2,
        on_unmatched=discarded.append,
    )

    assert result == expected
    assert discarded == [async_one, async_two]


def test_fc20_reader_does_not_parse_crc_collision_inside_opaque_payload() -> None:
    request_frame = add_crc(bytes.fromhex("012000000064"))
    payload = bytearray(range(200))
    payload[2:10] = add_crc(bytes.fromhex("008c00090400"))
    expected = add_crc(bytes([1, 0x20, 200]) + payload)
    serial = FakeSerial()
    serial.feed(expected)
    framer = RTUFramer(serial, char_time=0.001)
    observed: list[bytes] = []

    result = framer.read_fc20_frame(
        request_frame,
        timeout=0.2,
        on_unmatched=observed.append,
    )

    assert result == expected
    assert observed == []


def test_fc20_reader_reports_multiple_async_frames_before_response() -> None:
    request_frame = add_crc(bytes.fromhex("012000000064"))
    async_one = add_crc(bytes.fromhex("00090100"))
    async_two = add_crc(bytes.fromhex("00070100"))
    expected = add_crc(bytes([1, 0x20, 200]) + bytes(range(200)))
    serial = FakeSerial()
    serial.feed(async_one + async_two + expected)
    framer = RTUFramer(serial, char_time=0.001)
    observed: list[bytes] = []

    result = framer.read_fc20_frame(
        request_frame,
        timeout=0.2,
        on_unmatched=observed.append,
    )

    assert result == expected
    assert observed == [async_one, async_two]


def test_fc20_reader_accepts_exception_response() -> None:
    request_frame = add_crc(bytes.fromhex("012000000064"))
    expected = add_crc(bytes.fromhex("01a002"))
    serial = FakeSerial()
    serial.feed(expected)
    framer = RTUFramer(serial, char_time=0.001)

    assert framer.read_fc20_frame(request_frame, timeout=0.2) == expected


def test_matching_reader_honors_deadline_during_continuous_input() -> None:
    framer = RTUFramer(BusySerial(), char_time=0.001)

    started = time.perf_counter()
    result = framer.read_matching(lambda _frame: False, timeout=0.05)

    assert result == b""
    assert time.perf_counter() - started < 0.2


def test_reader_reports_unframed_serial_bytes() -> None:
    serial = FakeSerial()
    serial.feed(b"startup diagnostic\r\n")
    observed: list[tuple[bytes, str]] = []
    framer = RTUFramer(
        serial,
        char_time=0.001,
        on_unframed=lambda data, reason: observed.append((data, reason)),
    )

    assert framer.read_frame(timeout=0.01) == b""
    assert observed == [(b"startup diagnostic\r\n", "unframed_timeout")]
    assert framer.buf == bytearray()


def test_reader_reports_unframed_prefix_before_valid_frame() -> None:
    expected = bytes.fromhex("010100000001fdca")
    serial = FakeSerial()
    serial.feed(b"debug\r\n" + expected)
    observed: list[tuple[bytes, str]] = []
    framer = RTUFramer(
        serial,
        char_time=0.001,
        on_unframed=lambda data, reason: observed.append((data, reason)),
    )

    assert framer.read_frame(timeout=0.1) == expected
    assert observed == [(b"debug\r\n", "prefix_before_crc_frame")]


def test_conservative_reader_does_not_extract_frame_from_unframed_prefix() -> None:
    expected = bytes.fromhex("010100000001fdca")
    serial = FakeSerial()
    serial.feed(b"boot text\r\n" + expected)
    observed: list[tuple[bytes, str]] = []
    framer = RTUFramer(
        serial,
        char_time=0.001,
        on_unframed=lambda data, reason: observed.append((data, reason)),
        resync=False,
    )

    assert framer.read_frame(timeout=0.01) == b""
    assert observed == [(b"boot text\r\n" + expected, "unframed_timeout")]


def test_standard_reader_reports_unrelated_frame_without_losing_response() -> None:
    request_frame = request(0x03, 88, 1)
    async_frame = add_crc(bytes.fromhex("00090100"))
    expected = add_crc(bytes.fromhex("0103020131"))
    serial = FakeSerial()
    serial.feed(async_frame + expected)
    framer = RTUFramer(serial, char_time=0.001)
    observed: list[bytes] = []

    result = framer.read_standard_frame(
        request_frame,
        timeout=0.2,
        on_unmatched=observed.append,
    )

    assert result == expected
    assert observed == [async_frame]
