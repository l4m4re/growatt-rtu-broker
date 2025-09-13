import json
from growatt_broker.broker import modbus_crc, add_crc, crc_ok, parse_rtu


def test_crc_and_parse():
    body = bytes.fromhex('01030000000A')
    crc = modbus_crc(body)
    assert crc == 0xCDC5

    frame = add_crc(body)
    assert frame == body + bytes.fromhex('c5cd')
    assert crc_ok(frame)
    assert not crc_ok(frame[:-1])

    parsed = parse_rtu(frame)
    assert parsed == {'uid': 1, 'func': 3, 'len': 4, 'addr': 0, 'count': 10}
