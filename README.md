# Growatt RTU Broker (Prototype)

Man-in-the-middle / simulation backend layer for Growatt inverter Modbus RTU.

Current focus: providing a safe RS-485 proxy between Growatt hardware and multiple upstream clients while supplying dataset/capture helpers for the simulator project.

## Features (current snapshot)
- DatasetBackend: serve static register values from JSON dataset.
- Optional mutation (slow counter increment) for realism.
- CaptureBackend: wraps any backend & writes JSONL events per call.
- Production gateway (`growatt_broker/broker.py`) exposing:
  - Modbus TCP server (MBAP <-> RTU) with pacing and CRC checks.
  - ShineWiFi serial pass-through.
  - JSONL wire logging suitable for dataset compaction.
- Docker image & compose example for running on Home Assistant OS.

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
Run the production gateway against hardware:
```bash
growatt-broker \
  --inverter /dev/ttyUSB0 \
  --shine /dev/ttyUSB1 \
  --baud 9600 --bytes 8E1 \
  --tcp 0.0.0.0:5020 \
  --min-period 1.0 --rtimeout 1.5
```
See `docker-compose.yml` for the containerised equivalent.

## Capture file example (JSONL)
```json
{"ts": 1736868000.123, "op": "read_input", "unit": 1, "addr": 0,  "count": 4, "regs": [401,401,0,457]}
{"ts": 1736868001.125, "op": "read_holding", "unit": 1, "addr": 30, "count": 2, "regs": [100,0]}
```

## Roadmap
1. Refactor the broker script to consume the async backend interface (enables live capture mode via `CaptureBackend`).
2. Add virtual tty fan-out so multiple Home Assistant integrations can share one RS-485 adapter while using distinct device nodes.
3. Optional Shine-to-TCP bridge for mixed upstream clients.
4. Configurable mutation profiles (energy counters, PV curve).
5. Expand test coverage to include the production gateway.

## Deployment on Home Assistant OS

1. Copy this directory to `/mnt/data/supervisor/homeassistant/growatt-rtu-broker` using the Advanced SSH add-on.
2. Edit `docker-compose.yml` and set environment variables:
   - `INV_DEV` - inverter serial path (e.g. `/dev/serial/by-path/...`).
   - `SHINE_DEV` - Shine dongle path; point to the inverter path to disable Shine pass-through.
   - Optional: `TCP_BIND`, `MIN_PERIOD`, `RTIMEOUT`, `LOG_PATH`.
3. Run `docker compose up -d`.

The container binds port `5020` on the host (`network_mode: host`) and logs traffic to `/var/log/growatt_broker.jsonl`. Configure Home Assistant or other clients to use TCP transport against the host IP and port `5020`.

## Upcoming: Virtual serial fan-out

Multiple Growatt integrations inside Home Assistant currently collide when they open the same `/dev/ttyUSB*`. Planned work will create pseudo-terminal devices (e.g. `/run/growatt-broker/inverter1`) that forward frames through the broker's mutex-protected `Downstream`, letting several HA entries share the RS-485 bus while keeping unit IDs distinct and respecting pacing limits.

## Limitations
- The CLI demo and production gateway still live in separate code paths; a unified async implementation is on the roadmap above.
- Virtual tty fan-out is not yet implemented; use the TCP server for multiple inverters in the meantime.
- Error handling & unit coverage are minimal; expect prototype behaviour.

## Contributing
Keep broker concerns separate from HA integration code. Small, focused PRs (one feature at a time) encouraged.

MIT Licensed.
