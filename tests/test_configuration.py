from __future__ import annotations

import json
from pathlib import Path

from growatt_broker.broker import CacheGatewayService
from growatt_broker.cache_gateway import RegisterKey, add_crc
from growatt_broker.configuration import (
    ConfigurationError,
    InstallationConfig,
    SetupPlanObserver,
    load_installation_config,
    save_installation_config,
)


def _base_config() -> InstallationConfig:
    path = (
        Path(__file__).parents[1]
        / "configs/examples/growatt-min6000tl-xh-shinewifi-x-old.json"
    )
    return load_installation_config(path)


def test_installation_config_round_trips_atomically(tmp_path: Path) -> None:
    path = tmp_path / "installation.json"

    save_installation_config(_base_config(), path)

    loaded = load_installation_config(path)
    assert loaded.name == "Growatt MIN 6000TL-XH + ShineWiFi-X (old firmware)"
    assert loaded.inverter_transport.baud == 115200
    assert loaded.write_policy["shine"] == "transparent"
    assert loaded.poll_plan[0].key == RegisterKey(4, 3000, 125)
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == 1


def test_current_x2_profile_uses_observed_cadence_for_background_fallback() -> None:
    path = (
        Path(__file__).parents[1]
        / "configs/examples/growatt-min6000tl-xh-shinewilan-x2-learned.json"
    )

    config = load_installation_config(path)

    assert {block.interval_s for block in config.poll_plan} == {10.0}
    assert {block.max_age_s for block in config.poll_plan} == {30.0}


def test_setup_observer_exports_stable_unknown_block(tmp_path: Path) -> None:
    observer = SetupPlanObserver(_base_config())
    key = RegisterKey(3, 30000, 125)

    observer.observe(key, at=10.0)
    observer.observe(key, at=42.0)
    observer.observe(key, at=74.0)

    output = tmp_path / "candidate.json"
    candidate = observer.export(output)

    assert any(block.key == key for block in candidate.poll_plan)
    learned = next(block for block in candidate.poll_plan if block.key == key)
    assert learned.interval_s == 32.0
    assert candidate.operation_mode == "live"
    assert load_installation_config(output).source == "setup-observed"


def test_setup_observer_refines_existing_block() -> None:
    observer = SetupPlanObserver(_base_config())
    key = RegisterKey(3, 3000, 125)

    observer.observe(key, at=10.0)
    observer.observe(key, at=42.0)
    observer.observe(key, at=74.0)

    learned = next(
        block for block in observer.candidate().poll_plan if block.key == key
    )
    assert learned.interval_s == 32.0
    assert learned.max_age_s == 48.0


def test_setup_observer_ignores_unstable_block() -> None:
    observer = SetupPlanObserver(_base_config())
    key = RegisterKey(4, 31000, 125)

    observer.observe(key, at=10.0)
    observer.observe(key, at=42.0)
    observer.observe(key, at=100.0)

    assert all(block.key != key for block in observer.candidate().poll_plan)


def test_setup_observer_keeps_block_after_later_queue_jitter() -> None:
    observer = SetupPlanObserver(_base_config())
    key = RegisterKey(3, 30000, 125)

    for observed_at in (10.0, 42.0, 74.0, 1000.0):
        observer.observe(key, at=observed_at)
        observer.recommended_policy(key)

    learned = next(
        block for block in observer.candidate().poll_plan if block.key == key
    )
    assert learned.interval_s == 32.0


def test_setup_config_may_start_without_poll_plan() -> None:
    base = _base_config().to_dict()
    base["operation_mode"] = "setup"
    base["poll_plan"] = []

    config = InstallationConfig.from_dict(base)

    assert config.poll_plan == []


def test_setup_observer_exports_fc20_as_poll_block() -> None:
    observer = SetupPlanObserver(_base_config())
    key = RegisterKey(0x20, 0, 100)

    observer.observe(key, at=10.0)
    observer.observe(key, at=19.0)
    observer.observe(key, at=28.0)

    candidate = observer.candidate()
    learned = next(block for block in candidate.poll_plan if block.key == key)
    assert learned.interval_s == 9.0
    assert learned.max_age_s == 14.0


def test_invalid_config_requires_poll_plan() -> None:
    try:
        InstallationConfig.from_dict({"schema": 1})
    except ConfigurationError as exc:
        assert "poll_plan" in str(exc)
    else:
        raise AssertionError("missing poll_plan should be rejected")


def test_invalid_config_rejects_unknown_mode_and_write_policy() -> None:
    base = _base_config().to_dict()

    invalid_mode = dict(base, mode="unsupported")
    try:
        InstallationConfig.from_dict(invalid_mode)
    except ConfigurationError as exc:
        assert "broker mode" in str(exc)
    else:
        raise AssertionError("unsupported mode should be rejected")

    invalid_policy = dict(base, write_policy={"prod_tcp": "maybe"})
    try:
        InstallationConfig.from_dict(invalid_policy)
    except ConfigurationError as exc:
        assert "write_policy" in str(exc)
    else:
        raise AssertionError("invalid write policy should be rejected")


def test_setup_gateway_adds_stable_unknown_shine_block() -> None:
    class Downstream:
        def transact(self, request: bytes, **kwargs: object) -> bytes:
            count = int.from_bytes(request[4:6], "big")
            return add_crc(
                bytes([request[0], request[1], count * 2]) + b"\x00\x01" * count
            )

    observer = SetupPlanObserver(_base_config())
    gateway = CacheGatewayService(Downstream(), setup_observer=observer)
    request = add_crc(bytes.fromhex("01037530007d"))

    for observed_at in (10.0, 42.0, 74.0):
        result = gateway.handle_standard_request(
            request, client="SHINE", source="SHINE", now=observed_at
        )
        assert result.status == "served"

    assert RegisterKey(3, 30000, 125) in gateway._policy_by_key


def test_setup_gateway_adds_fc20_to_poll_plan() -> None:
    class Downstream:
        def transact(self, request: bytes, **kwargs: object) -> bytes:
            assert request == bytes.fromhex("01200000006481e6")
            return add_crc(bytes([1, 0x20, 200]) + b"\x00" * 200)

    observer = SetupPlanObserver(_base_config())
    gateway = CacheGatewayService(Downstream(), setup_observer=observer)
    request = bytes.fromhex("01200000006481e6")

    for observed_at in (10.0, 19.0, 28.0):
        result = gateway.handle_fc20(
            request, client="SHINE", source="SHINE", now=observed_at
        )
        assert result.status == "served"
        gateway.observe_shine_request(request, at=observed_at)

    assert gateway._fc20_policy is not None
    assert gateway._fc20_policy.key == RegisterKey(0x20, 0, 100)
