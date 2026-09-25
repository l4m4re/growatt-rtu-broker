# PiITM project instructions

**Release preparation status (2026-09-25):** the PiITM public README, roadmap,
live deployment guide, forensic runbook, evidence index, standalone test
configuration, pinned lint/test dependencies, and CI workflow are now in
place. Historical reports are under `docs/archive/`; generated logs and
reverse-engineering dumps were removed from the release tree. Remaining gates
are hardware acceptance evidence, image/tag publication, and separately
reviewed git-history cleanup.

This repository is the **Growatt Pi in the Middle (PiITM)** project. The
Raspberry Pi owns the physical inverter serial connection and mediates access
for Home Assistant, a Shine data logger, development tools, and controlled
reverse-engineering captures. PiITM is an observability and transport project;
the Home Assistant integration and the machine-readable register authority
remain separate projects.

These instructions describe the intended public project shape and the release
work still required. They do not authorize live inverter writes, firmware
flashes, or production changes. A live test must always have an explicit
scope, rollback command, and evidence location.

## Current implementation truth

The authoritative live data plane is `growatt_broker/broker.py` and its
`growatt-broker` console entry point. `growatt_broker/cli.py` is an older
dataset-demo CLI and is not the console entry point in `pyproject.toml`.

The implemented broker modes are:

| Mode | Implemented behavior |
| --- | --- |
| `legacy` | Physical inverter serial owner with optional direct Shine forwarding. |
| `cache` | One physical inverter owner plus shared native FC03/FC04 cache; no Shine client is required. |
| `cache+shine` | Shared cache and virtual Shine path. |
| `cache+shine-direct` | Shared broker scheduling with direct Shine forwarding. |
| `cache+shine-predictive` | Shared cache, observed Shine cadence, background native-block prefetch, and virtual Shine reads. |
| `legacy --shine-policy raw-transparent` | Diagnostic byte bridge only. It forwards serial bytes in both directions, disables Modbus TCP, and must never run concurrently with another owner of the serial ports. |

The cache is a register-block cache, not a duplicate TCP response cache. FC06
and FC10 writes are policy-controlled physical transactions. The current write
path invalidates overlapping blocks before the write, performs complete-block
readback after a successful acknowledgement, and reports readback failure
instead of claiming coherent success. This behavior is covered by the write
tests introduced in commit `5853fa0` and included in the
`live-broker-20260925` tag.

The deployed PiITM reference profile on 2026-09-25 used:

```text
mode:             cache+shine-predictive
inverter format:  115200 8N1
shine format:     115200 8N1
min-period:       0.5 s
RTU timeout:      8 s
FC20 timeout:     6 s
shine burst:      8
production TCP:   0.0.0.0:5020
development TCP:  0.0.0.0:5021
sniff stream:     0.0.0.0:5700
writes:           enabled by source policy
```

The RPi currently has two indistinguishable CH340-class USB serial adapters.
The inverter uses its stable `/dev/serial/by-id` alias. The ShineWiLan-X2 must
use its physical `/dev/serial/by-path` alias because it has no unique USB
serial number. No public example may depend on `ttyUSB0` or `ttyUSB1`.

The raw X2 bridge test on 2026-09-25 forwarded valid traffic for more than ten
minutes without timeout, CRC, exception, or serial-reopen events. The portal
became healthy after a Shine reset. This proves a useful diagnostic boundary,
not that the cache path is free of all physical read timeouts. The raw capture
is retained on the RPi under `/share/growatt-broker-shine-x2-raw-20260925/`
and is not a repository fixture.

## PiITM design contract

The following properties are part of the project contract and must remain
true when the code is reorganized:

1. There is one physical RTU owner. HA, Shine, development TCP, and background
   polling are queued through the same downstream scheduler.
2. A configured minimum inter-transaction period is applied to every physical
   request. Background polling intervals are separate from this transport
   spacing and must be documented separately.
3. Modbus framing, CRC validation, response association, and exception
   propagation are observable in structured JSONL logs.
4. A read cache may answer only from a coherent native block with an explicit
   freshness policy. A cache miss refreshes the complete native block.
5. A physical write is never represented as a normal cache read. Its
   acknowledgement, invalidation, readback, and failure state are logged.
6. Raw-transparent mode is a bounded forensic tool. It has no TCP command
   endpoint and must be the only serial owner for its test window.
7. Captures distinguish evidence from interpretation. Use these labels in
   reports: `PROVEN_LIVE`, `REPLAYED`, `STATIC_ANALYSIS`, `VENDOR_REFERENCE`,
   `HYPOTHESIS`, and `NOT_TESTED`.
8. PiITM does not become the register-map authority. Register names, scaling,
   access flags, and device-family claims belong in Growatt inverter info or
   Growatt_ModbusTCP and are linked from evidence reports.

## Documentation inventory and disposition

The current tree contains useful evidence, but it mixes public operating
guidance, historical experiment reports, unfinished plans, and local research
artefacts. The following is the release disposition for every current
documentation file.

### Rewrite as public PiITM documentation

| File | Finding and required action |
| --- | --- |
| `README.md` | Keep as the entry point, but rewrite around PiITM. Remove the unimplemented Modbus Workbench promises from the quick-start path, fix the `growatt-broker run/capture` examples (the console entry point is the live broker), document the actual mode table, X2 by-path rule, raw forensic boundary, and rollback. |
| `docs/ROADMAP.md` | Replace the old broad Workbench roadmap with the PiITM roadmap. Mark write-through cache coherence as implemented where the current tests prove it, and track remaining physical timeout, FC20 semantics, device-family, and release work separately. |
| `docs/ha_live_setup.md` | Make this the generic deployment guide. Remove private IPs, old Exar/X serial paths, stale `min-period=1`/timeout examples, and claims about the old logger. Add X2 by-path selection, host `/dev` visibility, the Docker namespace capture rule, normal and forensic profiles, and rollback. |
| `docs/SHINE_FIRMWARE_FORENSICS.md` | Retain as the forensic runbook, but update it for ShineWiLan-X2, the current image/tag, persistent `/share` captures, and the exact no-TCP safety boundary. |
| `docs/SIMULATOR.md` | Keep after verifying the command and dataset paths in an isolated broker environment. State clearly that the simulator is a PiITM test fixture and not a Home Assistant runtime dependency. |

### Keep as evidence, but mark as historical

These reports are valuable to developers, but their dated runtime claims must
not be read as the current deployment. Add a standard banner with date,
commit/image, hardware profile, and a link to this file; then move them under
`docs/archive/` in the cleanup phase.

| File | Historical subject |
| --- | --- |
| `docs/HA-DEV-2B_TRANSPORT_ACCEPTANCE.md` | Duplicate TCP suppression and early write-readiness gate. |
| `docs/HA-DEV-2C_SHINE_TRANSPORT.md` | Old ShineWiFi-X raw transport experiment that ended NOT GREEN. |
| `docs/HA-DEV-2D_FC20_REVERSE_ENGINEERING.md` | FC20 live evidence and semantic limits. |
| `docs/HA-DEV-2E_SHINE_DISCOVERY_FLUSH_AB.md` | Discovery flush A/B experiment. |
| `docs/HA-DEV-2_PYMODBUS_TRANSACTION_ID.md` | Legacy TCP transaction diagnosis; its response-cache discussion is not the current architecture. |
| `docs/HA-DEV-3A_VIRTUAL_DISCOVERY_LIVE.md` | Device-scoped virtual discovery proof. |
| `docs/HA-DEV-3B2_SHINE_3_1_0_5_DIFF_AND_IDENTITY.md` | Static old Shine firmware and identity analysis. |
| `docs/HA-DEV-3B_STOCK_SHINE_FIRMWARE_REVERSE_ENGINEERING.md` | Static stock firmware reverse engineering. |
| `docs/HA-DEV-3D_SESSION_CHECKPOINT.md` | Session checkpoint with old X firmware, paths, and timings. |
| `docs/HA-DEV-3E_BROKER_CONSOLIDATION_REVIEW.md` | 2026-09-11 consolidation review; useful provenance, not a current status page. |
| `docs/worktree-reconciliation.md` | Historical standalone-worktree reconciliation and local paths. |

### Update and retain as technical design records

| File | Required action |
| --- | --- |
| `docs/BROKER-4_AUTONOMOUS_CACHE_VIRTUAL_SHINE.md` | Preserve the design and test evidence, but change the old “candidate not deployed” wording where later commits supersede it and link to the current runtime contract. |
| `docs/HA-DEV-3A_CACHE_GATEWAY_ARCHITECTURE.md` | Retain as the cache design record; update its “production remains legacy” statement and distinguish cache mode from the current cache-plus-X2 deployment. |
| `docs/HA-DEV-3C_CACHE_GATEWAY_LIVE_INTEGRATION.md` | Retain as the staged rollout record, but update old image names, old Shine paths, and the current portal/reset and timeout observations. |
| `docs/HA-DEV-3C_SERIAL_RECOVERY.md` | Retain as a reusable recovery contract and add the identical-CH340/by-path X2 case. |

### Merge, rewrite, or remove from the release surface

| File | Finding and required action |
| --- | --- |
| `docs/ha_dev_2b_forensics.md` | Duplicates part of the 2B report and still describes the removed response-cache model. Merge the evidence into the historical report, then remove the duplicate. |
| `docs/live_log_analysis_plan.md` | An unfinished plan whose immediate steps are no longer the project entry point. Merge its useful parser tasks into the PiITM roadmap and remove the standalone file. |
| `docs/reverse_engineering_tasks.md` | Uses stale `doc/` paths and Home Assistant-specific names. Rewrite as a PiITM evidence workflow or archive it with corrected links. |
| `docs/data/*.json` | Keep compact machine-readable evidence with provenance and schema notes. Do not add raw captures, credentials, private IPs, or unbounded logs. |

The tracked root files `broker.log`, `broker-250925.log`,
`broker-260925.log`, `broker-260925-set-winter.log`, `rev_eng.txt`,
`rev_eng_full.txt`, and `rev_eng_words.txt` are research artefacts rather
than release documentation. The broker logs total tens of megabytes and
should be removed from the release tree and, before a public repository is
declared, from git history through a separately reviewed repository cleanup.
The two untracked logs must never be committed.

## Release-readiness gates

The repository is not release ready until all of these gates pass:

1. **Scope and metadata:** choose the public name (PiITM), supported Python
   versions, license/author metadata, repository URLs, and a versioning policy.
2. **Source truth:** make the live broker the only documented console entry
   point; either remove the unused demo backend CLI or give it a separate
   executable name and tests.
3. **Standalone tests:** run the broker tests outside the HA-core pytest
   plugin environment. Remove the implicit `socket_enabled` fixture dependency
   or provide a broker-owned fixture, pin compatible pytest/pytest-asyncio
   versions, and make a clean checkout pass.
4. **Static checks:** run Ruff/Black (or replace the old pre-commit revisions
   with current pinned versions) and add a small CI job for tests, lint, Docker
   build, simulator smoke test, and CLI help.
5. **Deployment truth:** replace private and stale serial paths in public docs
   with placeholders and document both the old ShineWiFi-X and new X2 profiles
   without conflating them. Include a tested rollback command.
6. **Evidence hygiene:** remove tracked logs and generated build artefacts,
   retain only small, reproducible samples, and document capture retention.
7. **Protocol boundaries:** publish what is proven versus inferred for FC20,
   discovery, H188, asynchronous frames, VPP registers, and each inverter
   family. Do not promote one MIN 6000TL-XH observation to a universal map.
8. **Hardware acceptance:** perform a bounded read-only test with HA, Shine,
   and the development endpoint; separately test one approved write/readback
   path with explicit rollback. Record image digest, CLI, timings, counters,
   and raw-capture location.
9. **Release handoff:** build and tag a reproducible image, publish a concise
   protocol/evidence index, and give Growatt_ModbusTCP developers the PiITM
   topology, request examples, limitations, and replayable samples.

## Cleanup and migration plan

Execute the following in separate reviewable commits:

### 1. Freeze and audit

- Record the current source SHA, live image digest, RPi command, serial aliases,
  mode, timings, ports, and rollback container.
- Run a claim scan for old paths, old image tags, `ttyUSB` examples, removed
  response-cache terminology, and private addresses.
- Mark every report `current`, `historical`, `static evidence`, or `remove`.

### 2. Establish the public PiITM surface

- Rewrite `README.md`, `docs/ROADMAP.md`, `docs/ha_live_setup.md`, and the
  forensic runbook.
- Add a short `docs/PIITM_PROTOCOL_AND_EVIDENCE.md` index linking to compact
  FC20/discovery/Shine evidence and to Growatt_ModbusTCP register work.
- Move historical reports under `docs/archive/` without changing their
  evidence, adding supersession banners instead.

### 3. Make the package independently usable

- Remove or isolate the stale demo `cli.py`.
- Decide whether the simulator belongs in this package (current recommendation:
  yes, as a transport test fixture) and ensure all simulator scripts use the
  same installed entry points.
- Add a broker-owned test configuration and CI; do not rely on HA-core plugins.
- Fix package metadata, supported Python versions, and dependency pins.

### 4. Clean repository history and artifacts

- Remove tracked live logs and generated `egg-info`/cache files from the
  working tree.
- Use a separately reviewed history rewrite or a new clean public repository
  if the large log blobs make cloning impractical. Never rewrite a shared
  branch without recording the old-to-new mapping and coordinating the remote
  change.

### 5. Validate the PiITM release candidate

- Run simulator and unit tests from a clean checkout.
- Build the Docker image and run CLI, raw-forensic, no-Shine, and
  cache-plus-Shine smoke tests against virtual serial fixtures.
- Perform a bounded RPi read-only canary, verify Shine portal continuity, then
  test one write/readback transaction only under an explicit rollback plan.
- Retain compact evidence and publish the image digest and release notes.

### 6. Share with Growatt_ModbusTCP developers

Provide a handoff containing:

- the one-owner PiITM topology and scheduler invariants;
- exact Modbus RTU/TCP request/response examples with timestamps removed or
  normalized;
- compact FC03/FC04/FC20/discovery samples and the evidence labels;
- the distinction between transport facts and register semantics;
- known X-versus-X2 logger differences, timing observations, and reset/recovery
  behavior;
- replay/simulator commands and the release image digest;
- a list of open questions suitable for coordinated reverse engineering.

## Working rules for future changes

- Keep live deployment notes separate from public generic instructions.
- Never commit credentials, private cloud tokens, full portal captures, or
  unbounded live logs.
- Do not infer a register meaning from address proximity or one UI label.
- Every new claim must cite a source capture, simulator fixture, static binary
  analysis, vendor document, or clearly mark itself as a hypothesis.
- A code change that affects physical ordering, timeout, framing, caching,
  writes, or Shine behavior needs a deterministic test and an updated runtime
  contract before release tagging.
- Keep Growatt register knowledge in Growatt inverter info/Growatt_ModbusTCP;
  PiITM should link to it and preserve transport evidence rather than fork the
  semantic map.
