# Modbus Workbench (formerly Growatt RTU Broker)

Modbus Workbench is a modular toolkit for bridging, monitoring, and simulating Modbus RTU/TCP networks. It began as a
purpose-built ShineWiFi/HA broker for Growatt inverters and is evolving into a general-purpose Modbus workbench that serves
both live deployments and lab/testing workflows. The immediate milestone is to let Home Assistant and a ShineWiFi dongle
share one inverter while recording and annotating traffic so we can discover undocumented registers safely.

> **Packaging note:** The Python package and CLI are still published as `growatt_broker` / `growatt-broker`. They will be
> renamed once the code base is fully aligned with the Modbus Workbench architecture.

## Project vision

* Provide a transport-agnostic gateway that multiplexes serial and TCP clients without violating Modbus timing rules.
* Capture, annotate, and replay Modbus frames so undocumented registers can be investigated offline.
* Offer a programmable simulation environment for integration testing, QA scripts, and education.

## Key scenarios and capabilities

- **ShineWiFi ↔ Home Assistant brokering** – Primary live use case: interpose between ShineWiFi-X and HA so both can talk to
  the same inverter while respecting pacing and CRC validation.
- **Bus sniffing & register discovery** – Mirror RS-485 traffic, decode frames, and enrich them with metadata so we can spot
  undocumented registers and units during live sessions.
- **Device scanning & dataset capture** – Poll register ranges or ingest sniffed data to build datasets that seed the simulator
  or document register maps.
- **Record / playback pipelines** – Store timestamped captures and replay them in real or accelerated time for unit tests,
  regression suites, or demos.
- **Scenario & state modelling** – Script register evolution (e.g., charge cycles or temperature ramps) or plug in simple
  state machines to mimic plant behaviour.
- **Scriptable QA interface** – Generate a typed API from device descriptions so automated tests can call helpers like
  `set("temperature", 50)` or `assert growatt("SoC") == 40`.
- **Serial ↔ TCP gatewaying** – Provide RTU-to-TCP bridges and virtual serial ports so mixed toolchains can coexist on one bus.
- **Fault-injection & resilience testing** *(new)* – Delay frames, drop packets, or corrupt CRCs on demand to validate client
  retry logic and stress error handling.
- **Education & interactive labs** *(new)* – Use datasets and scenarios to teach Modbus concepts or to onboard new
  maintainers without touching production hardware.

## Architecture building blocks

1. **Transport connectors** – pluggable drivers for physical serial ports (RS-485 USB), virtual pseudo-terminals, and TCP/UDP
   sockets. Each connector enforces pacing, retries, and framing.
2. **Device & register modelling** – declarative descriptions of units, register blocks, scaling, and engineering units that
   power both the simulator datasets and sniffed-frame annotations.
3. **Data plane services** – the asynchronous backend interface (`growatt_broker/backend.py`) that powers live serial access,
   dataset replay, and capture wrappers while keeping higher-level servers agnostic.
4. **Simulation & scenario engine** – dataset-backed simulator with mutators today, extended toward programmable scenarios,
   accelerated playback, and record/replay bundles.
5. **Tooling surfaces** – the CLI (`growatt-broker`), Python API, Docker image, and devcontainer helpers that expose the
   toolkit to Home Assistant, QA suites, or standalone Modbus users.

A deeper breakdown of components and responsibilities lives in [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Implementation roadmap (high-level)

### Phase 0 – Prototype foundation (underway)
- Keep the existing dataset backend, capture backend skeleton, and Modbus TCP server running for regression parity.
- Document current behaviours and tighten automated tests around the simulator utilities.

### Phase 1 – ShineWiFi/HA interposer & sniffer (current goal)
- Merge the CLI and async backend so the production gateway, dataset runner, and capture mode share the same code path.
- Add dual-port brokering (Shine serial + HA TCP) with pacing controls, fault-tolerant reconnects, and structured JSONL logs.
- Implement a live sniff mode that can annotate frames with register metadata and write compact capture bundles for analysis.
- Ship minimal tooling to compact captures into datasets and surface diff reports for newly observed registers.
- Provide setup, troubleshooting, and security guidance tailored to Home Assistant deployments.

### Phase 2 – Bus insight & dataset generation
- Build active scanners that walk configured register maps and detect deltas, units, and plausible scaling.
- Layer richer metadata onto capture files (engineering units, device type hints) and expose search/visualisation helpers.
- Support record-and-replay bundles with adjustable time compression for automated testing.

### Phase 3 – Scenario-driven simulation
- Introduce a scenario engine that drives mutators/state machines, enabling scripted charge cycles or thermal models.
- Allow composite virtual devices on one bus (multiple unit IDs) driven by shared scenarios.
- Expose APIs for QA scripts and CI runs to manipulate scenarios programmatically.

### Phase 4 – Automation & ecosystem
- Finalise virtual serial fan-out, RTU↔TCP bridging profiles, and optional MQTT/REST telemetry exporters.
- Publish reusable libraries (CRC/frame helpers, backend ABC) and package the toolkit for broader Modbus audiences.
- Harden deployment artifacts (Home Assistant add-on, Docker images, systemd units) and document upgrade paths.

## Repository layout & documentation

- `docs/ROADMAP.md` – detailed architecture notes and phased implementation plan.
- `growatt_broker/` – current Python package containing CLI, backend abstractions, simulator, and broker prototype.
- `tests/` – pytest suite covering simulator datasets, CRC helpers, and CLI basics.
- `scripts/` & `Dockerfile` – helper assets for running inside Home Assistant OS or containers.

## Quick start (editable install)

From the parent repo root (after initialising submodules):

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

Run with a static dataset for 10 seconds:

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

See [`docker-compose.yml`](docker-compose.yml) for the containerised equivalent.

## Capture file example (JSONL)

```json
{"ts": 1736868000.123, "op": "read_input", "unit": 1, "addr": 0,  "count": 4, "regs": [401,401,0,457]}
{"ts": 1736868001.125, "op": "read_holding", "unit": 1, "addr": 30, "count": 2, "regs": [100,0]}
```

## Deployment on Home Assistant OS

1. Copy this directory to `/mnt/data/supervisor/homeassistant/growatt-rtu-broker` using the Advanced SSH add-on.
2. Edit `docker-compose.yml` and set environment variables:
   - `INV_DEV` – inverter serial path (e.g. `/dev/serial/by-path/...`).
   - `SHINE_DEV` – Shine dongle path; point to the inverter path to disable Shine pass-through.
   - Optional: `TCP_BIND`, `MIN_PERIOD`, `RTIMEOUT`, `LOG_PATH`.
3. Run `docker compose up -d`.

The container binds port `5020` on the host (`network_mode: host`) and logs traffic to `/var/log/growatt_broker.jsonl`. Configure
Home Assistant or other clients to use TCP transport against the host IP and port `5020`.

## Limitations

- CLI demo and production gateway still share legacy code; the unified backend refactor is tracked in the roadmap above.
- Virtual serial fan-out and advanced sniffing are planned but not yet implemented.
- Error handling and unit coverage remain prototype-level; expect rough edges while the new architecture is assembled.

## Contributing

Keep broker/workbench concerns separate from Home Assistant integration code. Small, focused PRs (one feature at a time) are
encouraged—see [`docs/ROADMAP.md`](docs/ROADMAP.md) for the broader plan.

MIT Licensed.
