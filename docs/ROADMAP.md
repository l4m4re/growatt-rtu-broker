# PiITM roadmap

PiITM is the Growatt Pi in the Middle: a Raspberry Pi service that owns one
physical inverter RS-485 connection and safely shares it between Home
Assistant, Shine, development clients, and bounded forensic tools.

The repository is a transport, observability, simulator, and deployment
project. It is not the Home Assistant integration and it is not the authority
for register names or inverter semantics.

## Current status — 2026-09-25

### Implemented

- one queued physical RTU owner for all client sources;
- legacy, cache, cache-plus-Shine, and raw-transparent runtime profiles;
- Modbus TCP production/development listeners and JSONL sniff output;
- CRC/framing validation, response association, and Modbus exception
  propagation;
- native FC03/FC04 block cache with explicit freshness;
- FC06/FC10 cache invalidation plus complete-block read-after-write before a
  coherent write result is returned;
- Shine cadence observation and predictive native-block prefetch in the
  opt-in predictive profile;
- standalone package installation, simulator fixtures, tests, Black/Ruff
  checks, and CI workflow;
- bounded ShineWiLan-X2 raw bridge evidence on the live Raspberry Pi.

### Known limits

- FC20 is transported as an opaque request/response primitive. Its words do
  not have a universal semantic map in this repository;
- VPP registers, discovery payloads, H188/H209, asynchronous frames, and
  device-family differences remain evidence-gated;
- physical timeout and retry behavior still needs acceptance testing for each
  inverter and Shine firmware combination;
- the dataset demo CLI and live broker have separate code paths;
- there is no authentication or encryption on the Modbus TCP listeners;
- the live reference profile is proven on one installation and is not a
  generic configuration for every Growatt model.

## Release work

### 1. Public surface and packaging

- keep `growatt-broker` as the live console entry point;
- keep the dataset demo only as a tested simulator helper, or give it a
  separate command before expanding its scope;
- pin Black, Ruff, pytest, and pytest-asyncio compatibility in package extras;
- test clean installation on supported Python versions and build the Docker
  image in CI;
- choose a versioning and release-tag policy before publishing an image.

### 2. Deployment and rollback

- document stable `/dev/serial/by-path` selection for identical CH340
  adapters;
- keep normal cache-plus-Shine and raw-transparent forensic profiles
  separate;
- record the exact command, image digest, timing, ports, and evidence path for
  every live hardware test;
- keep a known-good image and command available before changing a live Pi;
- test read-only operation with HA, Shine, and the development listener before
  any approved write/readback test.

### 3. Evidence and protocol handoff

- keep compact, sanitised replay fixtures with provenance;
- preserve historical reports under `docs/archive/` with supersession banners;
- publish the evidence index in
  [`PIITM_PROTOCOL_AND_EVIDENCE.md`](PIITM_PROTOCOL_AND_EVIDENCE.md);
- hand transport evidence to Growatt_ModbusTCP while keeping register
  semantics in that project or Growatt inverter info;
- distinguish `PROVEN_LIVE`, `REPLAYED`, `STATIC_ANALYSIS`,
  `VENDOR_REFERENCE`, `HYPOTHESIS`, and `NOT_TESTED` in new reports.

### 4. Cache and scheduler hardening

- add explicit metrics for queue delay, physical transaction latency,
  cache age, stale blocks, retries, and write-readback failures;
- make background polling intervals visible and separate from the minimum
  inter-transaction spacing;
- test concurrent HA, Shine, and development reads under timeout and
  re-enumeration conditions;
- test write/readback clamping, rejected writes, and a failed readback without
  serving a stale value as coherent.

### 5. Device and firmware coverage

- reproduce the X2 transport test on a second controlled window;
- compare old ShineWiFi-X and ShineWiLan-X2 framing and timing without
  assuming they are interchangeable;
- add replay fixtures for known inverter families only when the model,
  firmware, and source capture are recorded;
- leave VPP and register interpretation to the Growatt register projects.

## Repository cleanup

The release surface should contain source, small fixtures, current operating
guides, and evidence indexes. Remove generated logs and build artefacts from
the release tree. Before declaring the repository public, remove the tracked
large logs and research dumps from git history in a separately reviewed
cleanup. The two untracked logs in the working tree must never be committed.

Historical HA-DEV reports remain useful to maintainers but are not current
instructions. They should live under `docs/archive/` and carry the source
commit, date, hardware profile, and supersession link.

## Suggested release gates

1. A clean checkout passes the standalone install, Ruff, Black, tests, and
   Docker build.
2. `growatt-broker --help` and the simulator help command work without HA-core.
3. Public docs contain no private IPs, credentials, unstable `ttyUSB` aliases,
   or obsolete response-cache claims.
4. A read-only live acceptance run records HA, Shine, development TCP, queue
   timing, CRC/exception counters, and rollback evidence.
5. One explicitly approved write/readback run confirms the cache contract and
   leaves the inverter in the documented safe state.
6. A release tag and image digest can be handed to Growatt_ModbusTCP developers
   together with the compact protocol/evidence index.
