from tools.compact_capture import compact


def test_compact_capture_keeps_last_value_per_register() -> None:
    events = [
        {"op": "read_input", "addr": 1, "regs": [10, 11]},
        {"op": "read_holding", "addr": 2, "regs": [20]},
        {"op": "read_input", "addr": 1, "regs": [12, 13]},
        {"op": "read_holding", "addr": 2, "regs": [22]},
    ]

    result = compact(events)

    assert result == {
        "input": {"1": 12, "2": 13},
        "holding": {"2": 22},
    }


def test_compact_capture_masks_register_values() -> None:
    result = compact(
        [{"op": "read_input", "addr": 1, "regs": [65536, -1]}]
    )

    assert result["input"] == {"1": 0, "2": 65535}
