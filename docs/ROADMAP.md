# PiITM roadmap

PiITM is the Growatt Pi in the Middle: a Raspberry Pi service that owns one
physical inverter RS-485 connection and safely shares it between Home
Assistant, Shine, and development clients.

The repository is a transport, observability, simulator, and deployment
project. It is not the Home Assistant integration and it is not the authority
for register names or inverter semantics.

## Current status — 2026-09-26

### Implemented

- one queued physical RTU owner for all client sources;
- legacy, cache, cache-plus-Shine, and cache-plus-Shine-direct runtime profiles;
- Modbus TCP production/development listeners and JSONL sniff output;
- CRC/framing validation, response association, and Modbus exception
  propagation;
- native FC03/FC04 block cache with explicit freshness;
- FC06/FC10 cache invalidation plus complete-block read-after-write before a
  coherent write result is returned;
- Shine request observation for diagnostics and setup learning;
- opportunistic read-only cache polling based on block age (nine-second target);
- explicit installation JSON configurations for the poll plan, transport,
  inverter, and logger combination;
- live mode with an approved installation plan and setup mode that observes
  Shine/TCP traffic, adapts the in-memory plan, and exports a candidate;
- standalone package installation, simulator fixtures, tests, Black/Ruff
  checks, and CI workflow;
- a read-only candidate canary built on the RPi, read on ports 5020/5021, and
  rolled back to the known-good container (see the acceptance record).
- a learned current-firmware X2 profile with the observed FC03/FC04 native
  blocks (the covered subset is omitted) is live on the RPi; FC20 is on-demand
  only and all three write paths are enabled and monitored with read-after-
  write evidence.

### Known limits

- FC20 is forwarded as an ordinary on-demand Modbus frame. Its words do not
  have a universal semantic map in this repository;
- VPP registers, discovery payloads, H188/H209, asynchronous frames, and
  device-family differences remain evidence-gated;
- physical timeout and retry behavior still needs acceptance testing for each
  inverter and Shine firmware combination;
- the dataset demo CLI and live broker have separate code paths;
- there is no authentication or encryption on the Modbus TCP listeners;
- the live reference profile is proven on one installation and is not a
  generic configuration for every Growatt model.
- setup candidates still require review and a controlled canary before live
  promotion; the broker does not infer register semantics from observations.

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
- record the exact command, image digest, timing, ports, and evidence path for
  every live hardware test;
- keep a known-good image and command available before changing a live Pi;
- test read-only operation with HA, Shine, and the development listener before
  any approved write/readback test.

Broker updates may be prepared, built, and verified offline at any time.
Applying an image to the live Raspberry Pi is a separate attended operation:
the user must explicitly arrange the window, remain present and able to
intervene, and observe the Growatt portal response. Do not deploy to the live
Pi or perform inverter writes while the user is away.

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
- make cache age, retry delay, and stale-block alarms visible;
- keep FC20 on-demand; it is not part of the cache poll plan;
- test concurrent HA, Shine, and development reads under timeout and
  re-enumeration conditions;
- the RTU reader's repeated-scan guard from `bec6440` is present on this
  branch. It skips a CRC rescan when the buffered bytes are unchanged; the
  framing regression suite passed 50 tests offline on 2026-11-27. This verifies
  the fixed-buffer case, not the cause of every historical timing outlier;
- the 2026-10-02 timing outlier discussed in the
  [archived Alchemy Growatt conversation](archive/Alchemy%20Growatt%20voortzetten.md)
  is not fully attributable from `frame_scan_ms` alone. That metric is reset
  before `pre_request_drain`, while `physical_latency_ms` starts after the
  request is written. A high scan total can therefore include pre-request
  buffer work. The event records no scan count, per-scan duration, buffer size,
  or UART chunk sequence, so the reported 6.62-second aggregate versus 382-ms
  physical latency does not prove which buffer or scan path caused it;
- before treating the historical outlier as fixed, add enough offline trace
  detail to separate pre-request draining from response framing and to count
  scans/bytes. Re-run the bounded regression suite before preparing another
  RPi image; applying it remains an attended, separately approved operation;
- cross-native-block reads now compose only same-`snapshot_id` cached blocks;
  otherwise the broker issues the exact requested range as one physical RTU
  request and fails closed if it cannot refresh. Offline regression coverage
  proves that mixed snapshots are not returned. Hardware support for arbitrary
  cross-block requests remains profile-specific and needs read-only validation;
- test write/readback clamping, rejected writes, and a failed readback without
  serving a stale value as coherent.

### 5. Device and firmware coverage

- reproduce the X2 transport test on a second controlled window;
- compare old ShineWiFi-X and ShineWiLan-X2 framing and timing without
  assuming they are interchangeable;
- add replay fixtures for known inverter families only when the model,
  firmware, and source capture are recorded;
- leave VPP and register interpretation to the Growatt register projects.

### 6. Firmware-update transport safety gate

This gate concerns Growatt inverter firmware transferred by the stock Shine
through PiITM to the inverter. Updating the PiITM software/container on the
Raspberry Pi is a separate deployment-and-rollback operation covered by
section 2.

Before practical use of a firmware update through a Shine/PiITM path, first
separate Shine logger OTA traffic from inverter firmware programming carried
over the serial link. Reconstruct the relevant message sequence, block
ordering, integrity checks, acknowledgements, timeouts, retries, aborts, and
recovery from offline firmware analysis and captured transactions. Treat
unknown update frames as opaque evidence; do not claim successful update
semantics from Modbus acknowledgements alone.

Use the Home Assistant `growatt_modbus.export_register_dump` action as a
read-only pre-update baseline and post-update diagnostic where useful. Its
lessons apply: keep broad vendor and firmware-derived ranges opt-in, explicitly
choose a block size the target can carry (the service defaults to 125 and only
inherits the entry's configured maximum when `block_size` is omitted), use the
selected entry's configured inter-request pacing where available, retain zeros
and per-range failures, and record the scan identity and settings. The standard
reader can fall back to single-register reads after a failed block probe, which
can greatly increase bus traffic and scan duration. A full scan can issue many
serial transactions and must never overlap an update session. The action's
exclusive lock pauses Growatt coordinators sharing its HA connection hub; it
does not exclude Shine traffic, PiITM cache polling, or other broker clients,
so it is not an update-safety lock.

When a config entry is selected, the action reads its persisted connection
settings even while the integration is disabled, preserving its tuned pacing
and block size while pausing that HA poller. Its lock and socket reset affect
only clients in that HA process. The ZABA22-derived diagnostic option sends
exactly 100 FC03 reads of 102 registers each and does not use the ordinary
single-register fallback. The ordinary FC03 reader avoids an accidental
102-register request inside the ZABA22 diagnostic dispatch window by reducing
such a chunk to 101. For broker-mediated scans, all physical requests must
still pass through the broker's RTU owner; record failed ranges and zero values
instead of omitting them.

The HA scanner holds its local gateway lock for the whole campaign, sleeps
between chunks, retries a transport failure once, and probes once with a
single-register read before downgrading subsequent reads after a rejected
block. Only the pacing and per-block recovery lessons transfer directly to a
shared broker. A broker-side scan must be a bounded low-priority sequence of
ordinary owner transactions, yielding between blocks so Shine/cloud traffic
can run; the HA lock cannot provide that system-wide scheduling guarantee.

A scan's `Read OK (zero value)` means the selected TCP endpoint returned zero;
it does not say whether PiITM served a fresh cache hit or performed a physical
RTU read. Record the cache decision, snapshot ID and age, plus the physical
request/response when a refresh occurs. In the 2026-11-14 staging scans through
the development endpoint, H30100 read as 1 and H30405, H30406 and H30475 read
as zero. The retained all-source write capture contains no matching successful
cache or RTU read events for those values, so their physical source remains
unresolved. Errors for other scan blocks do not establish the origin of these
VPP values. The scanner workflow and its limits are documented in the
[historical Alchemy Growatt conversation](archive/Alchemy%20Growatt%20voortzetten.md),
which is context rather than current policy.

The broker must then have an explicit update-transport policy that prevents
cache replies, request reordering, interleaving, or automatic retries from
altering an update session. It must preserve complete frames byte-for-byte,
report the difference between forwarded, acknowledged, and verified update
state, and fail closed on incomplete or ambiguous framing. Exercise
interruption and recovery cases against an isolated virtual endpoint before
any hardware trial. Add trace events that record transaction phase and
integrity result while excluding firmware payloads and credentials from
ordinary logs.

The practical-use gate passes only when a captured complete update session can
be replayed and fault-injected offline; the broker demonstrates exclusive
ownership of the inverter link for the session; interrupted, ambiguous, or
integrity-failing exchanges cannot be reported as successful; and the recovery
procedure and independent post-update version check are documented and
reproducible. Generic retries are forbidden until their exact update-protocol
semantics are established. A read-only scan, portal success indication, or
Modbus acknowledgement alone does not satisfy the gate.

Any physical update trial requires a separately approved, attended test
window, a device-specific vendor image and rollback/recovery procedure, a
pre-update identity/version check, and a way to verify the resulting version.
Record the image source and hash, verify any vendor signature that is provided,
and confirm compatibility with the exact inverter model, hardware revision,
and starting firmware. The recovery route must be validated for that target
and transport, with the required tools and image available before starting; if
recovery from an interrupted update cannot be established, the path is not
ready for practical use. Do not deliberately interrupt a production update
only to test recovery. The test operator must remain present and able to stop
the process; capture a read-only baseline beforehand and run any full
diagnostic scan only outside the transfer window. No unattended production
firmware update is in scope. No firmware update or RPi deployment is currently
authorized by this roadmap item.

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
