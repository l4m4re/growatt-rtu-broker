"""Cache-centric primitives used by the opt-in Growatt broker mode."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from statistics import median
from threading import RLock

CACHE_GATEWAY_DEFAULT_ENABLED = False


def modbus_crc(data: bytes) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def add_crc(body: bytes) -> bytes:
    return body + modbus_crc(body).to_bytes(2, "little")


def crc_ok(frame: bytes) -> bool:
    return len(frame) >= 4 and modbus_crc(frame[:-2]) == int.from_bytes(
        frame[-2:], "little"
    )


def build_read_response(request: bytes, words: Iterable[int]) -> bytes:
    """Build an RTU read response while preserving the request identity."""
    values = tuple(int(word) & 0xFFFF for word in words)
    if len(request) < 6 or request[1] not in (0x03, 0x04):
        raise ValueError("request is not a standard read")
    count = int.from_bytes(request[4:6], "big")
    if len(values) != count:
        raise ValueError("word count does not match request")
    body = bytes([request[0], request[1], count * 2])
    return add_crc(body + b"".join(word.to_bytes(2, "big") for word in values))


class CacheQuality(str, Enum):
    GOOD = "good"
    FAILED = "failed"
    UNKNOWN = "unknown"


class BrokerMode(str, Enum):
    SHINE_ABSENT = "shine_absent"
    SHINE_PRESENT = "shine_present"
    SHINE_LOST = "shine_lost"
    SHINE_RECOVERING = "shine_recovering"
    SHINE_DISCOVERY = "shine_discovery"


@dataclass(frozen=True)
class RegisterKey:
    """A physical contiguous Modbus register range."""

    function: int
    start: int
    count: int

    @property
    def end(self) -> int:
        return self.start + self.count

    def contains(self, other: RegisterKey) -> bool:
        return (
            self.function == other.function
            and self.start <= other.start
            and self.end >= other.end
        )


@dataclass(frozen=True)
class RegisterSnapshot:
    key: RegisterKey
    words: tuple[int, ...]
    captured_at: float
    generation: int
    snapshot_id: str
    source_transaction: str
    quality: CacheQuality = CacheQuality.GOOD

    def __post_init__(self) -> None:
        if len(self.words) != self.key.count:
            raise ValueError("snapshot word count does not match register range")
        if any(not 0 <= word <= 0xFFFF for word in self.words):
            raise ValueError("register words must be unsigned 16-bit values")

    def slice(self, key: RegisterKey) -> tuple[int, ...]:
        if not self.key.contains(key):
            raise ValueError("requested range is outside snapshot")
        offset = key.start - self.key.start
        return self.words[offset : offset + key.count]


@dataclass(frozen=True)
class CachedRead:
    requested: RegisterKey
    words: tuple[int, ...]
    snapshots: tuple[RegisterSnapshot, ...]
    age: float

    @property
    def generation(self) -> int:
        return max(snapshot.generation for snapshot in self.snapshots)

    @property
    def snapshot_ids(self) -> tuple[str, ...]:
        result: list[str] = []
        for snapshot in self.snapshots:
            if snapshot.snapshot_id not in result:
                result.append(snapshot.snapshot_id)
        return tuple(result)


class RegisterCache:
    """Raw-register cache that preserves block and snapshot coherence."""

    def __init__(self) -> None:
        self._blocks: dict[RegisterKey, RegisterSnapshot] = {}
        self._generation = 0

    @property
    def latest_generation(self) -> int:
        return self._generation

    def put_block(
        self,
        key: RegisterKey,
        words: Iterable[int],
        *,
        captured_at: float,
        source_transaction: str,
        snapshot_id: str | None = None,
        quality: CacheQuality = CacheQuality.GOOD,
    ) -> RegisterSnapshot:
        values = tuple(int(word) & 0xFFFF for word in words)
        if not 1 <= key.count <= 125 or len(values) != key.count:
            raise ValueError("snapshot word count does not match register key")
        self._generation += 1
        snapshot = RegisterSnapshot(
            key=key,
            words=values,
            captured_at=captured_at,
            generation=self._generation,
            snapshot_id=snapshot_id or f"generation-{self._generation}",
            source_transaction=source_transaction,
            quality=quality,
        )
        self._blocks[key] = snapshot
        return snapshot

    def snapshots(self) -> tuple[RegisterSnapshot, ...]:
        return tuple(self._blocks.values())

    def invalidate_overlapping(self, key: RegisterKey) -> tuple[RegisterKey, ...]:
        """Remove snapshots that may contain values changed by a write."""
        invalidated = tuple(
            snapshot_key
            for snapshot_key in self._blocks
            if snapshot_key.function == key.function
            and snapshot_key.start < key.end
            and key.start < snapshot_key.end
        )
        for snapshot_key in invalidated:
            del self._blocks[snapshot_key]
        return invalidated

    def _fresh_candidates(
        self, key: RegisterKey, *, now: float, max_age: float
    ) -> list[RegisterSnapshot]:
        candidates = []
        for snapshot in self._blocks.values():
            age = now - snapshot.captured_at
            if (
                snapshot.quality is CacheQuality.GOOD
                and age >= 0
                and age <= max_age
                and snapshot.key.contains(key)
            ):
                candidates.append(snapshot)
        return candidates

    def read(
        self,
        key: RegisterKey,
        *,
        now: float,
        max_age: float,
        allow_composed: bool = False,
        allow_mixed_snapshots: bool = False,
    ) -> CachedRead | None:
        candidates = self._fresh_candidates(key, now=now, max_age=max_age)
        if candidates:
            snapshot = min(
                candidates,
                key=lambda item: (item.key.count, -item.captured_at),
            )
            return CachedRead(
                requested=key,
                words=snapshot.slice(key),
                snapshots=(snapshot,),
                age=now - snapshot.captured_at,
            )
        if not allow_composed:
            return None
        return self._read_composed(
            key,
            now=now,
            max_age=max_age,
            allow_mixed_snapshots=allow_mixed_snapshots,
        )

    def _read_composed(
        self,
        key: RegisterKey,
        *,
        now: float,
        max_age: float,
        allow_mixed_snapshots: bool,
    ) -> CachedRead | None:
        remaining = key.start
        pieces: list[tuple[RegisterKey, RegisterSnapshot]] = []
        while remaining < key.end:
            needed = RegisterKey(key.function, remaining, key.end - remaining)
            candidates = self._fresh_candidates(needed, now=now, max_age=max_age)
            if not candidates:
                candidates = [
                    snapshot
                    for snapshot in self._fresh_candidates(
                        RegisterKey(key.function, remaining, 1),
                        now=now,
                        max_age=max_age,
                    )
                ]
            if not candidates:
                return None
            snapshot = min(
                candidates,
                key=lambda item: (item.key.count, -item.captured_at),
            )
            piece_end = min(snapshot.key.end, key.end)
            pieces.append(
                (
                    RegisterKey(key.function, remaining, piece_end - remaining),
                    snapshot,
                )
            )
            remaining = piece_end
        snapshot_ids = {snapshot.snapshot_id for _, snapshot in pieces}
        if not allow_mixed_snapshots and len(snapshot_ids) != 1:
            return None
        words: list[int] = []
        snapshots: list[RegisterSnapshot] = []
        for piece, snapshot in pieces:
            words.extend(snapshot.slice(piece))
            if snapshot not in snapshots:
                snapshots.append(snapshot)
        return CachedRead(
            requested=key,
            words=tuple(words),
            snapshots=tuple(snapshots),
            age=max(now - snapshot.captured_at for snapshot in snapshots),
        )


@dataclass(frozen=True)
class ClientReadRequest:
    client: str
    function: int
    start: int
    count: int

    @property
    def key(self) -> RegisterKey:
        return RegisterKey(self.function, self.start, self.count)


@dataclass(frozen=True)
class PhysicalPollRequest:
    key: RegisterKey
    reason: str
    requested_by: tuple[str, ...]
    sequence: int


@dataclass(frozen=True)
class GatewayResult:
    status: str
    client: str
    read: CachedRead | None = None
    physical_request: PhysicalPollRequest | None = None
    response: bytes | None = None
    reason: str | None = None


class PollCoordinator:
    """Coalesce client demand while retaining one physical owner."""

    def __init__(self, cache: RegisterCache, *, max_age: float) -> None:
        self.cache = cache
        self.max_age = max_age
        self._inflight: dict[int, list[ClientReadRequest]] = {}
        self._sequence = 0
        self.issued_requests: list[PhysicalPollRequest] = []

    def _find_covering(self, key: RegisterKey) -> PhysicalPollRequest | None:
        for request in self.issued_requests:
            if request.sequence in self._inflight and request.key.contains(key):
                return request
        return None

    def request(self, demand: ClientReadRequest, *, now: float) -> GatewayResult:
        cached = self.cache.read(demand.key, now=now, max_age=self.max_age)
        if cached is not None:
            return GatewayResult("served", demand.client, read=cached)
        covering = self._find_covering(demand.key)
        if covering is not None:
            self._inflight[covering.sequence].append(demand)
            return GatewayResult(
                "pending",
                demand.client,
                physical_request=covering,
                reason="coalesced_with_inflight_block",
            )
        self._sequence += 1
        poll = PhysicalPollRequest(
            key=demand.key,
            reason="cache_miss_or_stale",
            requested_by=(demand.client,),
            sequence=self._sequence,
        )
        self._inflight[poll.sequence] = [demand]
        self.issued_requests.append(poll)
        return GatewayResult("pending", demand.client, physical_request=poll)

    def complete(
        self,
        poll: PhysicalPollRequest,
        words: Iterable[int],
        *,
        now: float,
        source_transaction: str,
        snapshot_id: str | None = None,
    ) -> tuple[GatewayResult, ...]:
        demands = self._inflight.pop(poll.sequence)
        self.cache.put_block(
            poll.key,
            words,
            captured_at=now,
            source_transaction=source_transaction,
            snapshot_id=snapshot_id,
        )
        return tuple(
            GatewayResult(
                "served",
                demand.client,
                read=self.cache.read(
                    demand.key,
                    now=now,
                    max_age=self.max_age,
                ),
            )
            for demand in demands
        )

    def fail(
        self, poll: PhysicalPollRequest, *, reason: str
    ) -> tuple[GatewayResult, ...]:
        demands = self._inflight.pop(poll.sequence)
        return tuple(
            GatewayResult("failed", demand.client, reason=reason) for demand in demands
        )


@dataclass(frozen=True)
class OpaqueProtocolObject:
    request: bytes
    response: bytes
    captured_at: float
    generation: int
    quality: CacheQuality = CacheQuality.GOOD


class OpaqueProtocolCache:
    """Cache for proprietary frames such as Shine FC20."""

    def __init__(self) -> None:
        self._objects: dict[bytes, OpaqueProtocolObject] = {}
        self._generation = 0

    def put(
        self,
        request: bytes,
        response: bytes,
        *,
        captured_at: float,
    ) -> OpaqueProtocolObject:
        if not crc_ok(request) or not crc_ok(response):
            raise ValueError("opaque protocol objects must have valid CRCs")
        if len(request) < 2 or request[1] != 0x20:
            raise ValueError("request is not FC20")
        if len(response) < 3 or response[1] != 0x20:
            raise ValueError("response is not FC20")
        self._generation += 1
        value = OpaqueProtocolObject(
            request=request,
            response=response,
            captured_at=captured_at,
            generation=self._generation,
        )
        self._objects[request] = value
        return value

    def get(
        self, request: bytes, *, now: float, max_age: float
    ) -> OpaqueProtocolObject | None:
        value = self.latest(request)
        if value is None or value.quality is not CacheQuality.GOOD:
            return None
        if not 0 <= now - value.captured_at <= max_age:
            return None
        return value

    def latest(self, request: bytes) -> OpaqueProtocolObject | None:
        """Return the last validated object, regardless of age."""
        return self._objects.get(request)


@dataclass(frozen=True)
class PatternKey:
    function: int
    start: int
    count: int


@dataclass(frozen=True)
class PollPrediction:
    key: PatternKey
    due_at: float
    confidence: float


class ShinePatternObserver:
    """Learn bounded request order/timing without requiring exact periodicity."""

    def __init__(
        self, *, max_history: int = 64, jitter_tolerance: float = 0.25
    ) -> None:
        self.history: deque[tuple[PatternKey, float]] = deque(maxlen=max_history)
        self._cycle: list[PatternKey] = []
        self._sequence: tuple[PatternKey, ...] | None = None
        self._transitions: dict[tuple[PatternKey, PatternKey], deque[float]] = (
            defaultdict(lambda: deque(maxlen=8))
        )
        self._key_times: dict[PatternKey, deque[float]] = defaultdict(
            lambda: deque(maxlen=8)
        )
        self._last_seen: dict[PatternKey, float] = {}
        self._lock = RLock()
        self.jitter_tolerance = jitter_tolerance

    def observe(self, key: PatternKey, *, at: float) -> None:
        with self._lock:
            if self.history:
                previous_key, previous_at = self.history[-1]
                self._transitions[(previous_key, key)].append(at - previous_at)
            previous_at = self._last_seen.get(key)
            if previous_at is not None and at > previous_at:
                self._key_times[key].append(at - previous_at)
            self._last_seen[key] = at
            self.history.append((key, at))
            if not self._cycle:
                self._cycle = [key]
            elif key == self._cycle[0] and len(self._cycle) >= 2:
                candidate = tuple(self._cycle)
                if self._sequence is None:
                    self._sequence = candidate
                elif candidate != self._sequence:
                    self._sequence = None
                self._cycle = [key]
            else:
                self._cycle.append(key)

    @property
    def learned_sequence(self) -> tuple[PatternKey, ...] | None:
        with self._lock:
            return self._sequence

    def due_predictions(
        self,
        *,
        now: float,
        lead: float,
        minimum_samples: int = 2,
    ) -> tuple[PollPrediction, ...]:
        """Return block predictions that should be prefetched now.

        Per-key timing remains useful when the Shine occasionally inserts an
        undocumented or special transaction into its normal polling cycle.
        """
        with self._lock:
            predictions: list[PollPrediction] = []
            for key, samples in self._key_times.items():
                if len(samples) < minimum_samples:
                    continue
                typical = float(median(samples))
                if max(abs(sample - typical) for sample in samples) > self.jitter_tolerance:
                    continue
                last_seen = self._last_seen.get(key)
                if last_seen is None:
                    continue
                due_at = last_seen + typical
                if now < due_at - lead:
                    continue
                predictions.append(
                    PollPrediction(
                        key,
                        due_at,
                        min(1.0, len(samples) / 4),
                    )
                )
            return tuple(sorted(predictions, key=lambda item: item.due_at))

    def predict(self, *, now: float, lead: float = 0.1) -> PollPrediction | None:
        with self._lock:
            if self._sequence is None or not self.history:
                return None
            current, last_at = self.history[-1]
            try:
                index = self._sequence.index(current)
            except ValueError:
                return None
            next_key = self._sequence[(index + 1) % len(self._sequence)]
            samples = self._transitions.get((current, next_key))
            if not samples:
                return None
            typical = float(median(samples))
            if max(abs(sample - typical) for sample in samples) > self.jitter_tolerance:
                return None
            due_at = last_at + typical
            if now < due_at - lead:
                return None
            return PollPrediction(next_key, due_at, min(1.0, len(samples) / 4))


@dataclass(frozen=True)
class DiscoveryProfile:
    device_id: str
    request: bytes
    response: bytes


class ShineVirtualInverterAdapter:
    """Virtual serial endpoint; it never opens or owns the physical inverter."""

    def __init__(
        self,
        coordinator: PollCoordinator,
        *,
        discovery_profiles: Iterable[DiscoveryProfile],
        fc20_cache: OpaqueProtocolCache | None = None,
        request_handler: Callable[[bytes, float], GatewayResult] | None = None,
        fc20_handler: Callable[[bytes, float], GatewayResult] | None = None,
        passthrough_handler: Callable[[bytes, float], GatewayResult] | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.discovery_profiles = {
            profile.request: profile for profile in discovery_profiles
        }
        self.fc20_cache = fc20_cache or OpaqueProtocolCache()
        self.request_handler = request_handler
        self.fc20_handler = fc20_handler
        self.passthrough_handler = passthrough_handler
        self.mode = BrokerMode.SHINE_RECOVERING

    def _passthrough(self, frame: bytes, now: float, reason: str) -> GatewayResult:
        if self.passthrough_handler is not None:
            self.mode = BrokerMode.SHINE_PRESENT
            return self.passthrough_handler(frame, now)
        return GatewayResult("quarantined", "SHINE", reason=reason)

    def handle_request(self, frame: bytes, *, now: float) -> GatewayResult:
        if not crc_ok(frame) or len(frame) < 2:
            return GatewayResult("quarantined", "SHINE", reason="invalid_crc_or_frame")
        profile = self.discovery_profiles.get(frame)
        if profile is not None:
            if self.passthrough_handler is not None:
                self.mode = BrokerMode.SHINE_PRESENT
                return self._passthrough(
                    frame,
                    now,
                    f"physical_discovery:{profile.device_id}",
                )
            self.mode = BrokerMode.SHINE_PRESENT
            return GatewayResult(
                "discovery",
                "SHINE",
                response=profile.response,
                reason=profile.device_id,
            )
        function = frame[1]
        if function in (0x06, 0x10):
            return self._passthrough(frame, now, "write_not_allowed")
        if function == 0x20:
            if self.fc20_handler is not None:
                result = self.fc20_handler(frame, now)
                if result.status not in {"quarantined", "pending"}:
                    return result
                return self._passthrough(frame, now, result.reason or "fc20_miss")
            cached = self.fc20_cache.get(
                frame, now=now, max_age=self.coordinator.max_age
            )
            if cached is None:
                return self._passthrough(frame, now, "fc20_cache_miss")
            return GatewayResult(
                "served",
                "SHINE",
                response=cached.response,
                reason="fc20_opaque_cache",
            )
        if function not in (0x03, 0x04) or len(frame) != 8:
            return self._passthrough(frame, now, "unsupported_request")
        key = RegisterKey(
            function,
            int.from_bytes(frame[2:4], "big"),
            int.from_bytes(frame[4:6], "big"),
        )
        if frame[0] == 0:
            return self._passthrough(frame, now, "unprofiled_unit_zero_read")
        if self.request_handler is not None:
            self.mode = BrokerMode.SHINE_PRESENT
            return self.request_handler(frame, now)
        result = self.coordinator.request(
            ClientReadRequest("SHINE", function, key.start, key.count), now=now
        )
        if result.status == "served" and result.read is not None:
            response = build_read_response(frame, result.read.words)
            return GatewayResult(
                "served",
                "SHINE",
                read=result.read,
                response=response,
                reason=response.hex(),
            )
        self.mode = BrokerMode.SHINE_PRESENT
        return result

    def observe_disconnect(self) -> None:
        self.mode = BrokerMode.SHINE_LOST

    def observe_hotplug(self) -> None:
        self.mode = BrokerMode.SHINE_RECOVERING


def min_6000tl_xh_discovery_profile() -> DiscoveryProfile:
    """Return the explicitly device-scoped live MIN profile used in HA-DEV-3A."""

    return DiscoveryProfile(
        device_id="MIN 6000TL-XH / validated live profile",
        request=bytes.fromhex("0003002b0001f5d3"),
        response=bytes.fromhex("01030213ecb4f9"),
    )
