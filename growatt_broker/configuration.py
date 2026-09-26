"""Installation configuration and setup-mode learning primitives.

The broker deliberately keeps installation knowledge in one reviewable
configuration.  The configuration describes the inverter/logger combination
and the complete native blocks that should be refreshed; it is not a second
register-map authority.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from threading import RLock
from typing import Any

from .cache_gateway import CachePolicy, PatternKey, RegisterKey


class ConfigurationError(ValueError):
    """Raised when an installation configuration is malformed."""


@dataclass(frozen=True)
class TransportConfig:
    """Serial settings for one side of the PiITM installation."""

    device: str | None = None
    baud: int = 9600
    bytes: str = "8E1"

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "TransportConfig":
        value = value or {}
        return cls(
            device=value.get("device"),
            baud=int(value.get("baud", 9600)),
            bytes=str(value.get("bytes", "8E1")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"device": self.device, "baud": self.baud, "bytes": self.bytes}


@dataclass(frozen=True)
class PollBlockConfig:
    """One complete physical register block in an installation plan."""

    function: int
    start: int
    count: int
    name: str
    interval_s: float
    max_age_s: float
    service_class: str = "monitoring"
    priority: int = 5

    @property
    def key(self) -> RegisterKey:
        return RegisterKey(self.function, self.start, self.count)

    def to_cache_policy(self) -> CachePolicy:
        return CachePolicy(
            self.key,
            self.name,
            self.interval_s,
            self.max_age_s,
            self.service_class,
            self.priority,
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PollBlockConfig":
        try:
            function = int(value["function"])
            start = int(value["start"])
            count = int(value["count"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigurationError(f"invalid poll block: {value!r}") from exc
        if function not in (3, 4, 0x20) or not 1 <= count <= 125 or start < 0:
            raise ConfigurationError(f"invalid poll block range: {value!r}")
        if function == 0x20 and (start, count) != (0, 100):
            raise ConfigurationError("FC20 poll block must use start=0 and count=100")
        return cls(
            function=function,
            start=start,
            count=count,
            name=str(value.get("name", f"fc{function:02d}_{start}")),
            interval_s=max(0.1, float(value.get("interval_s", 60.0))),
            max_age_s=max(0.1, float(value.get("max_age_s", 180.0))),
            service_class=str(value.get("service_class", "monitoring")),
            priority=int(value.get("priority", 5)),
        )

    @classmethod
    def from_policy(cls, policy: CachePolicy) -> "PollBlockConfig":
        return cls(
            function=policy.key.function,
            start=policy.key.start,
            count=policy.key.count,
            name=policy.name,
            interval_s=policy.interval,
            max_age_s=policy.max_age,
            service_class=policy.service_class,
            priority=policy.priority,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "function": self.function,
            "start": self.start,
            "count": self.count,
            "name": self.name,
            "interval_s": self.interval_s,
            "max_age_s": self.max_age_s,
            "service_class": self.service_class,
            "priority": self.priority,
        }


@dataclass
class InstallationConfig:
    """One complete, user-reviewable PiITM installation configuration."""

    name: str
    inverter: dict[str, Any]
    logger: dict[str, Any]
    inverter_transport: TransportConfig
    logger_transport: TransportConfig
    poll_plan: list[PollBlockConfig]
    mode: str = "cache+shine"
    operation_mode: str = "live"
    source: str = "manual"
    schema: int = 1
    write_policy: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "InstallationConfig":
        if int(value.get("schema", 1)) != 1:
            raise ConfigurationError("unsupported installation config schema")
        mode = str(value.get("mode", "cache+shine"))
        if mode not in {
            "legacy",
            "cache",
            "cache+shine",
            "cache+shine-direct",
            "cache+shine-predictive",
        }:
            raise ConfigurationError(f"unsupported broker mode: {mode}")
        operation_mode = str(value.get("operation_mode", "live"))
        if operation_mode not in {"live", "setup"}:
            raise ConfigurationError(f"unsupported operation mode: {operation_mode}")
        raw_poll_plan = value.get("poll_plan", [])
        if not isinstance(raw_poll_plan, list):
            raise ConfigurationError("poll_plan must be an array")
        blocks = [PollBlockConfig.from_dict(item) for item in raw_poll_plan]
        if not blocks and operation_mode != "setup":
            raise ConfigurationError("installation config has no poll_plan blocks")
        raw_write_policy = value.get("write_policy", {})
        if not isinstance(raw_write_policy, dict):
            raise ConfigurationError("write_policy must be an object")
        write_policy = {
            str(name): str(setting) for name, setting in raw_write_policy.items()
        }
        valid_policies = {
            "prod_tcp": {"enabled", "disabled"},
            "dev_tcp": {"enabled", "disabled"},
            "shine": {"read-only", "transparent", "raw-transparent"},
        }
        for name, setting in write_policy.items():
            if name not in valid_policies or setting not in valid_policies[name]:
                raise ConfigurationError(
                    f"invalid write_policy value: {name}={setting}"
                )
        return cls(
            name=str(value.get("name", "installation")),
            inverter=dict(value.get("inverter", {})),
            logger=dict(value.get("logger", {})),
            inverter_transport=TransportConfig.from_dict(
                value.get("transport", {}).get("inverter")
            ),
            logger_transport=TransportConfig.from_dict(
                value.get("transport", {}).get("logger")
            ),
            poll_plan=blocks,
            mode=mode,
            operation_mode=operation_mode,
            source=str(value.get("source", "manual")),
            schema=1,
            write_policy=write_policy,
            metadata=dict(value.get("metadata", {})),
        )

    @classmethod
    def from_policies(
        cls,
        policies: tuple[CachePolicy, ...],
        *,
        name: str,
        inverter: dict[str, Any],
        logger: dict[str, Any],
        inverter_transport: TransportConfig,
        logger_transport: TransportConfig,
        mode: str = "cache+shine",
        source: str = "generated",
    ) -> "InstallationConfig":
        return cls(
            name=name,
            inverter=inverter,
            logger=logger,
            inverter_transport=inverter_transport,
            logger_transport=logger_transport,
            poll_plan=[PollBlockConfig.from_policy(policy) for policy in policies],
            mode=mode,
            source=source,
        )

    def policies(self) -> tuple[CachePolicy, ...]:
        return tuple(block.to_cache_policy() for block in self.poll_plan)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "name": self.name,
            "source": self.source,
            "mode": self.mode,
            "operation_mode": self.operation_mode,
            "inverter": self.inverter,
            "logger": self.logger,
            "write_policy": self.write_policy,
            "transport": {
                "inverter": self.inverter_transport.to_dict(),
                "logger": self.logger_transport.to_dict(),
            },
            "poll_plan": [block.to_dict() for block in self.poll_plan],
            "metadata": self.metadata,
        }

    def copy_with_blocks(
        self, blocks: list[PollBlockConfig], *, source: str = "setup-observed"
    ) -> "InstallationConfig":
        value = self.to_dict()
        value["poll_plan"] = [block.to_dict() for block in blocks]
        value["source"] = source
        value["operation_mode"] = "live"
        return InstallationConfig.from_dict(value)


def load_installation_config(path: str | os.PathLike[str]) -> InstallationConfig:
    """Load one JSON installation configuration."""
    config_path = Path(path)
    try:
        with config_path.open(encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"cannot load config {config_path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError("installation config must contain a JSON object")
    return InstallationConfig.from_dict(value)


def save_installation_config(
    config: InstallationConfig, path: str | os.PathLike[str]
) -> None:
    """Atomically save a reviewable installation configuration."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(config.to_dict(), stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


class SetupPlanObserver:
    """Learn a candidate installation plan without changing the live config."""

    def __init__(self, base: InstallationConfig) -> None:
        self.base = base
        self._lock = RLock()
        self._times: dict[PatternKey, list[float]] = {}
        self._last_seen: dict[PatternKey, float] = {}
        self._counts: dict[PatternKey, int] = {}
        self._recommended: dict[PatternKey, PollBlockConfig] = {}

    def observe(self, key: RegisterKey, *, at: float | None = None) -> None:
        if key.function not in (3, 4, 0x20):
            return
        observed_at = time.monotonic() if at is None else at
        pattern = PatternKey(key.function, key.start, key.count)
        with self._lock:
            previous = self._last_seen.get(pattern)
            if previous is not None and observed_at > previous:
                self._times.setdefault(pattern, []).append(observed_at - previous)
            self._last_seen[pattern] = observed_at
            self._counts[pattern] = self._counts.get(pattern, 0) + 1

    def candidate(self) -> InstallationConfig:
        """Return the base plan extended with observed stable request blocks."""
        with self._lock:
            samples = {key: tuple(values[-8:]) for key, values in self._times.items()}
            counts = dict(self._counts)
            recommended = dict(self._recommended)
        blocks = {block.key: block for block in self.base.poll_plan}
        metadata = dict(self.base.metadata)
        for pattern, intervals in samples.items():
            learned = recommended.get(pattern)
            if learned is not None:
                blocks[learned.key] = learned
                continue
            if counts.get(pattern, 0) < 3 or not intervals:
                continue
            typical = float(median(intervals))
            if max(abs(value - typical) for value in intervals) > max(
                1.0, typical * 0.2
            ):
                continue
            key = RegisterKey(pattern.function, pattern.start, pattern.count)
            interval = max(1.0, typical)
            observed_block = PollBlockConfig(
                function=key.function,
                start=key.start,
                count=key.count,
                name=f"shine_fc{key.function:02d}_{key.start}_{key.count}",
                interval_s=interval,
                max_age_s=max(interval * 1.5, interval + 5.0),
                service_class="shine",
                priority=1,
            )
            # Setup mode also refines a provisional block.  This is what
            # makes a candidate useful when the initial plan was only a guess.
            blocks[key] = observed_block
        ordered = sorted(
            blocks.values(),
            key=lambda block: (block.priority, block.function, block.start),
        )
        candidate = self.base.copy_with_blocks(ordered)
        candidate.metadata = metadata
        return candidate

    def recommended_policy(self, key: RegisterKey) -> CachePolicy | None:
        """Return a policy after two stable observations of one request."""
        pattern = PatternKey(key.function, key.start, key.count)
        with self._lock:
            intervals = tuple(self._times.get(pattern, ()))
        if len(intervals) < 2:
            return None
        typical = float(median(intervals[-8:]))
        if max(abs(value - typical) for value in intervals[-8:]) > max(
            1.0, typical * 0.2
        ):
            return None
        interval = max(1.0, typical)
        policy = PollBlockConfig(
            function=key.function,
            start=key.start,
            count=key.count,
            name=f"shine_fc{key.function:02d}_{key.start}_{key.count}",
            interval_s=interval,
            max_age_s=max(interval * 1.5, interval + 5.0),
            service_class="shine",
            priority=1,
        )
        with self._lock:
            self._recommended[pattern] = policy
        return policy.to_cache_policy()

    def export(self, path: str | os.PathLike[str]) -> InstallationConfig:
        candidate = self.candidate()
        save_installation_config(candidate, path)
        return candidate
