# Growatt RTU Broker (Prototype)

Man-in-the-middle / simulation backend layer for Growatt inverter Modbus RTU.

Current focus: backend abstraction + dataset + capture. Live serial backend and
Modbus TCP serving will be integrated next.

## Features (current snapshot)
- DatasetBackend: serve static register values from JSON dataset.
- Optional mutation (slow counter increment) for realism.
- CaptureBackend: wraps any backend & writes JSONL events per call.
- CLI subcommands:
  - `run`     : demo loop polling a few registers (placeholder for server)
  - `capture` : same, plus JSONL logging of every operation.

## Installation (editable)
From parent repo root (after submodule init):
```bash
pip install -e external/growatt-rtu-broker
```

## Dataset JSON shape
```jsonc
{
  "holding": {"30": 100, "31": 0},
  "input":  {"0": 401},
  "_source": "scan provenance optional"
}
```

## Usage examples
Run with static dataset for 10 seconds:
```bash
growatt-broker run --mode dataset --dataset datasets/min_6000xh_tl.json --duration 10
```
Capture all operations:
```bash
growatt-broker capture --mode dataset --dataset datasets/min_6000xh_tl.json --out session.jsonl --duration 5
```
Enable simple mutation (auto-increment some values):
```bash
growatt-broker run --mode dataset --dataset datasets/min_6000xh_tl.json --mutate
```

## Capture file example (JSONL)
```json
{"ts": 1736868000.123, "op": "read_input", "unit": 1, "addr": 0,  "count": 4, "regs": [401,401,0,457]}
{"ts": 1736868001.125, "op": "read_holding", "unit": 1, "addr": 30, "count": 2, "regs": [100,0]}
```

## Roadmap
1. Implement LiveSerialBackend (real RS-485 transact + pacing).
2. Add Modbus TCP server translating MBAP <-> RTU via selected backend.
3. Add Shine serial endpoint (optional) pass-through.
4. Introduce capture compaction script (to dataset JSON) in parent repo.
5. Add docker image & (optional) Home Assistant add-on wrapper.
6. Configurable mutation profiles (energy counters, PV curve). 
7. Unit tests for backend + capture logic.

## Limitations
- No live serial implementation yet.
- No TCP server yet; current demo just exercises backend calls.
- Error handling & logging minimal (prototype stage).

## Contributing
Keep broker concerns separate from HA integration code. Small, focused PRs (one feature at a time) encouraged.

MIT Licensed.