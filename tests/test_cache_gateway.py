from __future__ import annotations

import time

from growatt_broker.broker import CacheGatewayService
from growatt_broker.cache_gateway import (
    CACHE_GATEWAY_DEFAULT_ENABLED,
    BrokerMode,
    ClientReadRequest,
    GatewayResult,
    OpaqueProtocolCache,
    PatternKey,
    PollPrediction,
    PollCoordinator,
    RegisterCache,
    RegisterKey,
    ShinePatternObserver,
    ShineVirtualInverterAdapter,
    add_crc,
    min_6000tl_xh_discovery_profile,
)


def _coordinator(max_age: float = 5.0) -> tuple[RegisterCache, PollCoordinator]:
    cache = RegisterCache()
    return cache, PollCoordinator(cache, max_age=max_age)


def _adapter() -> ShineVirtualInverterAdapter:
    _, coordinator = _coordinator()
    return ShineVirtualInverterAdapter(
        coordinator,
        discovery_profiles=(min_6000tl_xh_discovery_profile(),),
    )


def test_exact_device_scoped_discovery_generates_expected_response() -> None:
    profile = min_6000tl_xh_discovery_profile()
    result = _adapter().handle_request(profile.request, now=0.0)

    assert result.status == "discovery"
    assert result.response == profile.response
    assert result.reason == profile.device_id


def test_discovery_uses_physical_passthrough_when_available() -> None:
    profile = min_6000tl_xh_discovery_profile()
    seen: list[bytes] = []

    def passthrough(frame: bytes, _now: float) -> GatewayResult:
        seen.append(frame)
        return GatewayResult(
            "served", "SHINE", response=profile.response, reason="physical_passthrough"
        )

    _, coordinator = _coordinator()
    adapter = ShineVirtualInverterAdapter(
        coordinator,
        discovery_profiles=(profile,),
        passthrough_handler=passthrough,
    )

    result = adapter.handle_request(profile.request, now=0.0)

    assert result.status == "served"
    assert result.response == profile.response
    assert result.reason == "physical_passthrough"
    assert seen == [profile.request]


def test_unit_zero_discovery_accepts_physical_unit_one_response() -> None:
    profile = min_6000tl_xh_discovery_profile()
    calls: list[tuple[bytes, bool]] = []

    class RecordingDownstream:
        def transact(self, request: bytes, **kwargs: object) -> bytes:
            calls.append((request, bool(kwargs["standard_modbus"])))
            return profile.response

    gateway = CacheGatewayService(RecordingDownstream())

    result = gateway.handle_shine_passthrough(profile.request)

    assert result.status == "served"
    assert result.response == profile.response
    assert calls == [(profile.request, False)]


def test_unrelated_unit_zero_request_is_not_answered_as_discovery() -> None:
    request = add_crc(bytes.fromhex("0003002c0001"))

    result = _adapter().handle_request(request, now=0.0)

    assert result.status == "quarantined"
    assert result.response is None
    assert result.reason == "unprofiled_unit_zero_read"


def test_fresh_cache_serves_three_clients_without_physical_poll() -> None:
    cache, coordinator = _coordinator()
    cache.put_block(
        RegisterKey(4, 3000, 125),
        range(125),
        captured_at=10.0,
        source_transaction="fc04-3000",
    )

    results = tuple(
        coordinator.request(
            ClientReadRequest(client, 4, 3000, 20),
            now=10.5,
        )
        for client in ("SHINE", "HA", "DEV")
    )

    assert [result.status for result in results] == ["served"] * 3
    assert coordinator.issued_requests == []


def test_overlapping_subset_is_served_from_larger_coherent_block() -> None:
    cache, coordinator = _coordinator()
    cache.put_block(
        RegisterKey(4, 3000, 125),
        range(125),
        captured_at=20.0,
        source_transaction="fc04-3000",
        snapshot_id="snapshot-1",
    )

    result = coordinator.request(
        ClientReadRequest("DEV", 4, 3020, 20),
        now=20.1,
    )

    assert result.status == "served"
    assert result.read is not None
    assert result.read.words == tuple(range(20, 40))
    assert result.read.snapshot_ids == ("snapshot-1",)


def test_stale_cache_creates_one_shared_refresh() -> None:
    cache, coordinator = _coordinator(max_age=5.0)
    cache.put_block(
        RegisterKey(4, 3000, 125),
        range(125),
        captured_at=0.0,
        source_transaction="old",
    )
    demand = ClientReadRequest("HA", 4, 3000, 20)

    first = coordinator.request(demand, now=10.0)
    second = coordinator.request(
        ClientReadRequest("DEV", 4, 3000, 20),
        now=10.0,
    )

    assert first.status == "pending"
    assert second.status == "pending"
    assert first.physical_request == second.physical_request
    assert len(coordinator.issued_requests) == 1


def test_duplicate_simultaneous_demand_is_coalesced() -> None:
    _, coordinator = _coordinator()
    demand = ClientReadRequest("HA", 3, 0, 125)

    first = coordinator.request(demand, now=0.0)
    second = coordinator.request(demand, now=0.0)

    assert first.physical_request == second.physical_request
    assert len(coordinator.issued_requests) == 1


def test_refresh_failure_does_not_masquerade_as_fresh_data() -> None:
    cache, coordinator = _coordinator()
    demand = ClientReadRequest("HA", 4, 3000, 125)
    pending = coordinator.request(demand, now=100.0)
    assert pending.physical_request is not None

    failed = coordinator.fail(pending.physical_request, reason="timeout")
    retry = coordinator.request(demand, now=100.1)

    assert failed[0].status == "failed"
    assert failed[0].reason == "timeout"
    assert retry.status == "pending"
    assert len(coordinator.issued_requests) == 2
    assert cache.read(demand.key, now=100.1, max_age=5.0) is None


def test_snapshot_generation_and_cross_block_coherence_are_deterministic() -> None:
    cache = RegisterCache()
    first = cache.put_block(
        RegisterKey(4, 3000, 125),
        range(125),
        captured_at=1.0,
        source_transaction="first",
        snapshot_id="batch-1",
    )
    second = cache.put_block(
        RegisterKey(4, 3125, 125),
        range(125, 250),
        captured_at=1.1,
        source_transaction="second",
        snapshot_id="batch-1",
    )
    composed = cache.read(
        RegisterKey(4, 3100, 50),
        now=2.0,
        max_age=5.0,
        allow_composed=True,
    )

    assert (first.generation, second.generation) == (1, 2)
    assert composed is not None
    assert composed.snapshot_ids == ("batch-1",)
    assert composed.words == tuple(range(100, 150))


def test_shine_presence_changes_schedule_mode_not_physical_owner() -> None:
    adapter = _adapter()
    profile = min_6000tl_xh_discovery_profile()

    adapter.observe_hotplug()
    adapter.handle_request(profile.request, now=0.0)
    adapter.observe_disconnect()

    assert adapter.mode == BrokerMode.SHINE_LOST
    assert adapter.coordinator is not None
    assert not hasattr(adapter, "physical_inverter")


def test_pattern_observer_predicts_with_timing_jitter() -> None:
    first = PatternKey(4, 3000, 125)
    second = PatternKey(3, 180, 20)
    observer = ShinePatternObserver(jitter_tolerance=0.2)
    for key, at in (
        (first, 0.0),
        (second, 1.0),
        (first, 2.1),
        (second, 3.0),
        (first, 4.0),
    ):
        observer.observe(key, at=at)

    prediction = observer.predict(now=4.85, lead=0.2)

    assert observer.learned_sequence == (first, second)
    assert prediction is not None
    assert prediction.key == second


def test_pattern_observer_falls_back_after_sequence_change() -> None:
    first = PatternKey(4, 3000, 125)
    second = PatternKey(3, 180, 20)
    changed = PatternKey(4, 3125, 125)
    observer = ShinePatternObserver()
    for key, at in (
        (first, 0.0),
        (second, 1.0),
        (first, 2.0),
        (changed, 3.0),
        (first, 4.0),
    ):
        observer.observe(key, at=at)

    assert observer.learned_sequence is None
    assert observer.predict(now=4.5) is None


def test_pattern_observer_predicts_due_block_without_fixed_sequence() -> None:
    first = PatternKey(4, 3000, 125)
    second = PatternKey(4, 3125, 125)
    observer = ShinePatternObserver(jitter_tolerance=0.2)
    for key, at in (
        (first, 0.0),
        (second, 1.0),
        (first, 2.0),
        (second, 3.0),
        (first, 4.0),
        (second, 5.0),
    ):
        observer.observe(key, at=at)

    predictions = observer.due_predictions(now=6.5, lead=0.2)

    assert predictions == (PollPrediction(first, 6.0, 0.5),)


def test_fc20_is_cached_and_replayed_as_opaque_crc_valid_data() -> None:
    request = bytes.fromhex("01200000006481e6")
    response = add_crc(bytes([1, 0x20, 200]) + bytes(range(200)))
    cache = OpaqueProtocolCache()

    stored = cache.put(request, response, captured_at=10.0)
    replay = cache.get(request, now=10.5, max_age=5.0)

    assert stored.response == response
    assert replay is not None
    assert replay.response == response


def test_fc06_and_fc10_do_not_enter_read_cache_path() -> None:
    cache, coordinator = _coordinator()
    adapter = ShineVirtualInverterAdapter(
        coordinator,
        discovery_profiles=(min_6000tl_xh_discovery_profile(),),
    )
    single = add_crc(bytes.fromhex("010600bc0001"))
    multiple = add_crc(bytes.fromhex("011000bc00020400010002"))

    single_result = adapter.handle_request(single, now=0.0)
    multiple_result = adapter.handle_request(multiple, now=0.0)

    assert single_result.status == "quarantined"
    assert multiple_result.status == "quarantined"
    assert single_result.reason == "write_not_allowed"
    assert multiple_result.reason == "write_not_allowed"
    assert coordinator.issued_requests == []
    assert cache.snapshots() == ()


def test_unknown_shine_function_is_quarantined_by_default() -> None:
    adapter = _adapter()
    unknown_function = add_crc(bytes.fromhex("012100000001"))

    result = adapter.handle_request(unknown_function, now=0.0)

    assert result.status == "quarantined"
    assert result.response is None


def test_unknown_shine_function_uses_physical_passthrough_when_configured() -> None:
    request = add_crc(bytes.fromhex("012100000001"))
    response = add_crc(bytes.fromhex("01210100"))
    seen: list[bytes] = []

    def passthrough(frame: bytes, _now: float) -> GatewayResult:
        seen.append(frame)
        return GatewayResult("served", "SHINE", response=response)

    _, coordinator = _coordinator()
    adapter = ShineVirtualInverterAdapter(
        coordinator,
        discovery_profiles=(min_6000tl_xh_discovery_profile(),),
        passthrough_handler=passthrough,
    )

    result = adapter.handle_request(request, now=0.0)

    assert result.status == "served"
    assert result.response == response
    assert seen == [request]


def test_shine_writes_use_physical_passthrough_when_configured() -> None:
    request = add_crc(bytes.fromhex("010600bc0001"))
    response = request
    seen: list[bytes] = []

    def passthrough(frame: bytes, _now: float) -> GatewayResult:
        seen.append(frame)
        return GatewayResult("served", "SHINE", response=response)

    _, coordinator = _coordinator()
    adapter = ShineVirtualInverterAdapter(
        coordinator,
        discovery_profiles=(),
        passthrough_handler=passthrough,
    )

    result = adapter.handle_request(request, now=0.0)

    assert result.status == "served"
    assert result.response == response
    assert seen == [request]


def test_successful_shine_write_invalidates_overlapping_cache() -> None:
    downstream = _FakeDownstream()
    request = add_crc(bytes.fromhex("010600bc0001"))
    downstream.transact = lambda _request, **_kwargs: request  # type: ignore[method-assign]
    gateway = CacheGatewayService(downstream)
    gateway.cache.put_block(
        RegisterKey(3, 180, 20),
        range(20),
        captured_at=10.0,
        source_transaction="shine-read",
    )

    result = gateway.handle_shine_passthrough(request)

    assert result.status == "served"
    assert gateway.cache.read(
        RegisterKey(3, 180, 1), now=10.1, max_age=5.0
    ) is None


def test_cache_gateway_is_explicitly_non_default() -> None:
    assert CACHE_GATEWAY_DEFAULT_ENABLED is False


class _FakeDownstream:
    def __init__(self) -> None:
        self.requests: list[bytes] = []

    def transact(self, request: bytes, **_kwargs: object) -> bytes:
        self.requests.append(request)
        function = request[1]
        start = int.from_bytes(request[2:4], "big")
        count = int.from_bytes(request[4:6], "big")
        words = [(start + offset) & 0xFFFF for offset in range(count)]
        return add_crc(
            bytes([request[0], function, count * 2])
            + b"".join(word.to_bytes(2, "big") for word in words)
        )


def test_gateway_reads_from_one_native_block_and_replays_subsets() -> None:
    downstream = _FakeDownstream()
    gateway = CacheGatewayService(downstream)
    first = add_crc(bytes.fromhex("01040bb80001"))
    second = add_crc(bytes.fromhex("01040bcc0001"))

    first_result = gateway.handle_standard_request(
        first, client="HA", source="PROD_TCP"
    )
    second_result = gateway.handle_standard_request(
        second, client="DEV", source="DEV_TCP"
    )

    assert first_result.status == "served"
    assert second_result.status == "served"
    assert first_result.read is not None
    assert second_result.read is not None
    assert first_result.read.words == (3000,)
    assert second_result.read.words == (3020,)
    assert downstream.requests == [bytes.fromhex("01040bb8007db22a")]


def test_cache_gateway_reads_do_not_require_a_shine_client() -> None:
    downstream = _FakeDownstream()
    gateway = CacheGatewayService(downstream)

    production = gateway.handle_standard_request(
        add_crc(bytes.fromhex("01040bb80001")),
        client="HA",
        source="PROD_TCP",
    )
    development = gateway.handle_standard_request(
        add_crc(bytes.fromhex("01040bcc0001")),
        client="DEV",
        source="DEV_TCP",
    )

    assert production.status == "served"
    assert development.status == "served"
    assert [request.hex() for request in downstream.requests] == [
        "01040bb8007db22a"
    ]


def test_gateway_composes_a_read_across_native_block_boundaries() -> None:
    downstream = _FakeDownstream()
    gateway = CacheGatewayService(downstream)
    request = add_crc(bytes.fromhex("01040c1d0020"))

    result = gateway.handle_standard_request(
        request, client="HA", source="PROD_TCP"
    )

    assert result.status == "served"
    assert result.read is not None
    assert result.read.words == tuple(range(3101, 3133))
    assert downstream.requests == [
        bytes.fromhex("01040bb8007db22a"),
        bytes.fromhex("01040c35007d2375"),
    ]

    repeated = gateway.handle_standard_request(
        request, client="HA", source="PROD_TCP"
    )

    assert repeated.status == "served"
    assert downstream.requests == [
        bytes.fromhex("01040bb8007db22a"),
        bytes.fromhex("01040c35007d2375"),
    ]


def test_ha_uses_a_recent_shine_snapshot_before_refreshing() -> None:
    downstream = _FakeDownstream()
    gateway = CacheGatewayService(downstream)
    key = RegisterKey(4, 3000, 125)
    gateway.cache.put_block(
        key,
        range(125),
        captured_at=10.0,
        source_transaction="shine-read",
    )
    request = add_crc(bytes.fromhex("01040bb80001"))

    result = gateway.handle_standard_request(
        request,
        client="HA",
        source="PROD_TCP",
        now=50.0,
    )

    assert result.status == "served"
    assert result.read is not None
    assert result.read.words == (0,)
    assert downstream.requests == []


def test_gateway_fc20_is_fetched_once_then_replayed() -> None:
    downstream = _FakeDownstream()
    request = bytes.fromhex("01200000006481e6")
    response = add_crc(bytes([1, 0x20, 200]) + bytes(range(200)))

    def transact(request: bytes, **_kwargs: object) -> bytes:
        downstream.requests.append(request)
        return response

    downstream.transact = transact  # type: ignore[method-assign]
    gateway = CacheGatewayService(downstream)

    first = gateway.handle_fc20(request, client="SHINE", source="SHINE", now=10.0)
    second = gateway.handle_fc20(request, client="SHINE", source="SHINE", now=10.5)

    assert first.status == "served"
    assert second.status == "served"
    assert first.response == response
    assert second.response == response
    assert downstream.requests == [request]


def test_fc20_timeout_is_reported_as_physical_passthrough_failure() -> None:
    downstream = _FakeDownstream()
    downstream.transact = lambda _request, **_kwargs: b""  # type: ignore[method-assign]
    gateway = CacheGatewayService(downstream)
    request = bytes.fromhex("01200000006481e6")

    result = gateway.handle_fc20(request, client="SHINE", source="SHINE", now=10.0)

    assert result.status == "failed"
    assert result.response is None
    assert result.reason == "physical passthrough timeout"


def test_due_background_refresh_does_not_serve_the_old_fresh_entry() -> None:
    downstream = _FakeDownstream()
    gateway = CacheGatewayService(downstream)
    key = RegisterKey(4, 3000, 125)
    gateway.cache.put_block(
        key,
        range(125),
        captured_at=100.0,
        source_transaction="old",
    )

    read, error = gateway._read_words(
        key,
        client="PREFETCH",
        source="BACKGROUND",
        now=115.0,
        force_refresh=True,
    )

    assert error is None
    assert read is not None
    assert downstream.requests == [bytes.fromhex("01040bb8007db22a")]
    assert read.words == tuple(range(3000, 3125))


def test_predictive_prefetch_refreshes_next_native_shine_block() -> None:
    downstream = _FakeDownstream()
    gateway = CacheGatewayService(downstream, predictive_prefetch=True)
    request = add_crc(bytes.fromhex("01040bb8007d"))

    for at in (0.0, 10.0, 20.0):
        gateway.observe_shine_request(request, at=at)

    gateway._run_predictive_prefetch(28.0)

    assert downstream.requests == [bytes.fromhex("01040bb8007db22a")]
    assert gateway.cache.read(
        RegisterKey(4, 3000, 125),
        now=time.monotonic(),
        max_age=5.0,
    ) is not None
