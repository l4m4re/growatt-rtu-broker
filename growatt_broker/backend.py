"""Backend abstractions for Growatt RTU Broker.

Provides a uniform async interface so the broker front‑end (Modbus TCP server
or Shine pass‑through) can switch between:
  - LiveSerialBackend (real hardware; to be implemented separately)
  - DatasetBackend (static dataset for simulation)
  - CaptureBackend (wraps another backend and writes JSONL events)

Dataset JSON shape expected:
{
  "holding": {"30": 100, "31": 0, ...},
  "input": {"0": 401, ...},
  "_source": "optional provenance"
}
"""

from __future__ import annotations
import abc
import json
import asyncio
import time
import pathlib
from typing import Dict, List, Iterable, Any

Number = int  # registers values are ints 0..65535 (unsigned representation)


class Backend(abc.ABC):
    """Abstract backend definition."""

    @abc.abstractmethod
    async def read_input(self, unit: int, address: int, count: int) -> List[Number]: ...
    @abc.abstractmethod
    async def read_holding(
        self, unit: int, address: int, count: int
    ) -> List[Number]: ...
    @abc.abstractmethod
    async def write_single(self, unit: int, address: int, value: Number) -> None: ...
    @abc.abstractmethod
    async def write_multiple(
        self, unit: int, address: int, values: Iterable[Number]
    ) -> None: ...


class DatasetBackend(Backend):
    """Static dataset implementation.

    Missing addresses return 0. Dataset is stored in memory as two dicts.
    Optional mutators can be supplied to produce dynamic values.
    """

    def __init__(
        self, dataset_path: str | pathlib.Path, *, mutate: bool = False
    ) -> None:
        p = pathlib.Path(dataset_path)
        raw = json.loads(p.read_text(encoding="utf-8"))
        self.holding: Dict[int, int] = {
            int(k): int(v) for k, v in raw.get("holding", {}).items()
        }
        self.input: Dict[int, int] = {
            int(k): int(v) for k, v in raw.get("input", {}).items()
        }
        self._mutate = mutate
        self._start = time.time()

    def _maybe_mutate(self, table: Dict[int, int], addr: int, val: int) -> int:
        if not self._mutate:
            return val
        # Simple deterministic mutator: increment energy-like counters slowly
        # (heuristic: addresses > 100 and value non-negative)
        elapsed = int(time.time() - self._start)
        if addr > 100 and val >= 0:
            return (val + elapsed) & 0xFFFF
        return val

    async def read_input(self, unit: int, address: int, count: int) -> List[int]:
        return [
            self._maybe_mutate(self.input, a, self.input.get(a, 0))
            for a in range(address, address + count)
        ]

    async def read_holding(self, unit: int, address: int, count: int) -> List[int]:
        return [
            self._maybe_mutate(self.holding, a, self.holding.get(a, 0))
            for a in range(address, address + count)
        ]

    async def write_single(
        self, unit: int, address: int, value: int
    ) -> None:  # allow ephemeral overriding
        self.holding[address] = value & 0xFFFF

    async def write_multiple(
        self, unit: int, address: int, values: Iterable[int]
    ) -> None:
        for offset, v in enumerate(values):
            self.holding[address + offset] = int(v) & 0xFFFF


class CaptureBackend(Backend):
    """Wrapper backend that records each operation to a JSONL file.

    Events:
      {"ts": <unix>, "op": "read_input",  "unit":1,"addr":0,"count":10,"regs":[...]}
      {"ts": <unix>, "op": "write_single","unit":1,"addr":45,"value":1234}
    """

    def __init__(self, inner: Backend, jsonl_path: str | pathlib.Path) -> None:
        self._inner = inner
        self._path = pathlib.Path(jsonl_path)
        # Create file early so existence signals capture mode.
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def _log(self, payload: Dict[str, Any]) -> None:
        async with self._lock:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    async def read_input(self, unit: int, address: int, count: int) -> List[int]:
        regs = await self._inner.read_input(unit, address, count)
        await self._log(
            {
                "ts": time.time(),
                "op": "read_input",
                "unit": unit,
                "addr": address,
                "count": count,
                "regs": regs,
            }
        )
        return regs

    async def read_holding(self, unit: int, address: int, count: int) -> List[int]:
        regs = await self._inner.read_holding(unit, address, count)
        await self._log(
            {
                "ts": time.time(),
                "op": "read_holding",
                "unit": unit,
                "addr": address,
                "count": count,
                "regs": regs,
            }
        )
        return regs

    async def write_single(self, unit: int, address: int, value: int) -> None:
        await self._inner.write_single(unit, address, value)
        await self._log(
            {
                "ts": time.time(),
                "op": "write_single",
                "unit": unit,
                "addr": address,
                "value": value & 0xFFFF,
            }
        )

    async def write_multiple(
        self, unit: int, address: int, values: Iterable[int]
    ) -> None:
        vals = [int(v) & 0xFFFF for v in values]
        await self._inner.write_multiple(unit, address, vals)
        await self._log(
            {
                "ts": time.time(),
                "op": "write_multiple",
                "unit": unit,
                "addr": address,
                "values": vals,
            }
        )


# Placeholder for future live implementation (kept minimal to satisfy imports)
class LiveSerialBackend(Backend):
    def __init__(self, *args, **kwargs):  # pragma: no cover (stub)
        raise NotImplementedError("LiveSerialBackend not implemented in this snapshot")

    async def read_input(self, unit: int, address: int, count: int) -> List[int]: ...
    async def read_holding(self, unit: int, address: int, count: int) -> List[int]: ...
    async def write_single(self, unit: int, address: int, value: int) -> None: ...
    async def write_multiple(
        self, unit: int, address: int, values: Iterable[int]
    ) -> None: ...
