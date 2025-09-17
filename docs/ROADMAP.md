# Modbus Workbench – design & implementation roadmap

Modbus Workbench evolves the original Growatt RTU Broker into a full-featured Modbus toolkit for live operations,
monitoring, and testing. This document captures the architecture direction and the staged delivery plan that will guide
implementation.

## Design principles

1. **One core backend, many frontends** – Serial bridges, TCP servers, sniffers, and simulators should all build on the same
   asynchronous backend abstractions so new features immediately benefit every entry point.
2. **Data first** – Every live interaction should be capturable, replayable, and annotatable. Captures, scans, and datasets are
   treated as first-class artefacts.
3. **Composable tooling** – CLI commands, Python APIs, and container images expose small, composable operations that can be
   scripted into QA pipelines or automated agents.
4. **Protocol neutrality** – While the project starts with Growatt hardware, nothing in the design assumes Growatt-only
   semantics. Device descriptions, metadata, and transports must support other vendors and topologies.
5. **Safe-by-default live operation** – Pacing, CRC validation, and logging guardrails are mandatory whenever the toolkit touches
   real hardware.

## Architectural building blocks

### Transport adapters
- RS-485 serial (physical) with configurable baud/byte settings, pacing, and retry policies.
- Virtual pseudo-terminals to fan out one master connection to multiple logical ports (future).
- Modbus TCP/RTU-over-TCP connectors for remote access and tunnelling.
- Optional UDP or MQTT exporters for telemetry mirroring (later phase).

### Backend services
- `LiveSerialBackend` talks to real devices via pyserial/async-serial.
- `DatasetBackend` replays JSON datasets with optional mutation hooks.
- `CaptureBackend` wraps any backend to emit JSONL capture events.
- Future backends include a `SnifferBackend` for passive monitoring and a `ScenarioBackend` for scripted state machines.

### Device knowledge base
- Declarative register maps with metadata (type, unit, scaling, enums, access flags).
- Device families and inheritance to support multiple inverter/PLC variants.
- Conversion helpers that turn register metadata into human-friendly decoded values for logs and dashboards.

### Data acquisition & storage
- Structured JSONL capture format (timestamp, op, unit, addr/count, registers, annotations).
- Compaction utilities that fold captures into dataset snapshots with provenance tags.
- Register discovery reports highlighting unseen addresses, value ranges, and unit assumptions.

### Simulation & scenario engine
- Tick-driven mutation framework (already prototyped) extended with declarative scenarios (ramps, cycles, PID approximations).
- Record & playback modes supporting real-time or accelerated timelines.
- Fault injection hooks (delay, CRC tampering, frame drops) for resilience testing.

### Interfaces & packaging
- `growatt-broker` CLI powering run/capture/simulate/sniff subcommands.
- Python API for embedding into external tooling or tests.
- Docker images and HA add-on manifests for turnkey deployment.
- Devcontainer scripts to simplify contributor workflows.

## Implementation phases

### Phase 0 – Prototype foundation (stabilise current code)
**Objectives**
- Preserve existing behaviours while documentation and tests are refreshed.
- Ensure datasets and simulator utilities remain usable during the transition.

**Key work items**
- Audit existing CLI modes and tests; convert brittle scripts into pytest coverage where practical.
- Capture current configuration knobs in documentation.
- Establish continuous integration hooks (lint, formatting) specific to the broker package.

**Deliverables**
- Updated README and roadmap (this document).
- Baseline tests covering CRC helpers, dataset loading, and CLI smoke runs.

### Phase 1 – ShineWiFi/HA interposer & sniffer (current focus)
**Objectives**
- Let Home Assistant and the ShineWiFi dongle share a Growatt inverter safely.
- Produce actionable capture files that surface undocumented registers and their context.

**Key work items**
- Refactor `growatt_broker.broker` to consume the async backend, eliminating duplicated live/device logic.
- Harden the dual-port broker (serial Shine + Modbus TCP) with pacing, retry, and reconnect strategies and configurable
  safety limits (minimum poll interval, max outstanding requests).
- Extend `CaptureBackend` to include request/response timings, CRC status, and source port metadata.
- Implement a passive sniff mode: attach to RS-485 receive line, parse frames, and annotate them using the register metadata.
- Ship a `growatt-broker sniff` CLI that can run alongside the broker for man-in-the-middle monitoring.
- Provide dataset compaction & diff tooling (`capture -> dataset -> report`) that highlights newly observed registers.
- Document ShineWiFi/HA deployment recipes, troubleshooting steps, and safety caveats (e.g., bus exclusivity, grounding).

**Deliverables**
- Unified CLI with `run`, `capture`, and `sniff` modes sharing backend plumbing.
- JSONL capture schema reference and example analysis notebook/script.
- Updated Docker/devcontainer recipes tested against real hardware.

### Phase 2 – Bus insight & dataset generation
**Objectives**
- Automate discovery of register behaviour and scaling across different inverter families.
- Provide tooling to interrogate devices without permanent live connections.

**Key work items**
- Active scanning routines with rate limiting and selectable register ranges.
- Annotation pipeline that maps register values to engineering units/descriptions using the knowledge base.
- CLI/GUI reports for new vs known registers, value distributions, and suspected enumerations.
- Dataset bundle format capturing scan metadata, firmware version, and optional Shine serial number.

**Deliverables**
- `growatt-broker scan` command producing dataset bundles plus summary reports.
- Knowledge base schema and initial library of device descriptions.
- Enhanced simulator datasets annotated with `_source`, firmware, and register semantics.

### Phase 3 – Scenario-driven simulation & playback
**Objectives**
- Recreate complex operational patterns without physical hardware.
- Enable accelerated regression suites and behavioural demos.

**Key work items**
- Scenario definition DSL/JSON (states, transitions, register assignments, timers).
- Scheduler capable of running multiple scenarios simultaneously (multi-device or multi-unit setups).
- Time-warp controls for playback (real-time, accelerated, stepped) and API hooks for tests to advance time deterministically.
- Libraries of reusable scenarios (charge cycle, grid outage, thermal ramp, inverter fault injection).

**Deliverables**
- `growatt-broker simulate` enhancements to load scenarios alongside datasets.
- Example Home Assistant automation tests running against accelerated playback.
- Documentation & tutorials for writing custom scenarios.

### Phase 4 – Automation & ecosystem integrations
**Objectives**
- Turn Modbus Workbench into a drop-in tool for QA teams and power users beyond Home Assistant.

**Key work items**
- Virtual serial fan-out (pty multiplexer) so multiple RTU masters can coexist.
- Optional protocol bridges (Modbus↔MQTT/REST) for telemetry export.
- Scriptable QC/QA interface generating strongly-typed helpers from device descriptions.
- Packaging & release automation (PyPI, Docker Hub, HA add-on store).

**Deliverables**
- Stable 1.x release branded as Modbus Workbench.
- Published API reference and example automation notebooks/test suites.
- Hardened deployment guides and upgrade playbooks.

## Cross-cutting concerns
- **Observability** – structured logging, metrics, and optional OpenTelemetry exporters for long-running brokers.
- **Security** – transport encryption for TCP modes (TLS/stunnel), credential handling, and principle of least privilege for
  file paths and serial devices.
- **Testing** – CI coverage for protocol edge cases, scenario regression tests, and hardware-in-the-loop smoke tests.
- **Documentation** – living guides for dataset provenance, register annotations, and troubleshooting (for both HA users and
  broader Modbus audiences).

## Scenario backlog (beyond initial scope)
- **Grid fault drills** – simulate brownouts or frequency excursions to test DER controller reactions.
- **Performance benchmarking** – measure how many concurrent TCP clients or scenario engines the toolkit can support on
  constrained hardware.
- **Educational labs** – step-by-step tutorials that combine sniffing, scanning, and scenario scripting to teach Modbus
  diagnostics to new contributors.

Contributions and feedback on this roadmap are welcome—open issues or PRs in the Modbus Workbench repository to discuss
priorities or propose additional phases.
