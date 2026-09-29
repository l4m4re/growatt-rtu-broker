# PiITM protocol and evidence index

This page is the handoff surface for developers who want to understand the
Growatt Pi in the Middle transport without importing the Home Assistant
integration or the register map. It records what the broker can transport and
what remains device-specific research.

## Evidence labels

Use one of these labels in reports and fixtures:

- `PROVEN_LIVE`: observed on the current or explicitly identified physical
  setup, with a capture or command recorded;
- `REPLAYED`: reproduced from a saved capture or simulator fixture;
- `STATIC_ANALYSIS`: inferred from firmware or binary inspection;
- `VENDOR_REFERENCE`: stated by a vendor document or tool;
- `HYPOTHESIS`: useful interpretation that still needs an experiment;
- `NOT_TESTED`: no evidence in this project.

A transport observation does not establish register meaning. Device model,
firmware, scaling, writable ranges, and VPP semantics remain in Growatt
inverter info or Growatt_ModbusTCP.

## Current PiITM topology

The Raspberry Pi owns the inverter RS-485 adapter. HA TCP, the Shine logger,
and a development TCP client are queued by one scheduler. The current X2
reference profile is:

```text
inverter: 115200 8N1, stable /dev/serial/by-path alias
Shine X2: 115200 8N1, stable /dev/serial/by-path alias
minimum physical transaction period: none; the single-owner queue still serializes transactions
RTU timeout: 0.85 s for all Modbus reads, including FC20
serial reopen threshold: 10 consecutive standard physical timeouts; background
The cache poller submits one oldest block at a time. Each physical transaction
returns to the shared queue before another background block is submitted, so a
pending write or on-demand request can run between blocks. A failed background
block is skipped while the remaining candidates are considered, preventing an
immediate retry loop. The broker does not retry a timed-out physical
transaction; any retry is initiated by the requesting client.
TCP: 5020 production, 5021 development, 5700 JSONL sniff stream
mode: cache+shine
```

The native FC03/FC04 block identities and write permissions are selected by one installation JSON
configuration. Refresh timing is broker policy: the read-only cache poller selects the oldest block at a nine-second target age. FC20 is on-demand only. The old ShineWiFi-X/old-firmware plan and the current X2
candidate are kept under [`configs/examples/`](../configs/examples/). A setup
run can observe unknown standard blocks and export a candidate, but promotion
to live mode remains a human review and canary step.

Both USB adapters are CH340-class and have no unique serial number. The
physical path is therefore part of the deployment configuration. A `ttyUSB`
number is not a stable identity.

## Request examples

These are wire examples from the historical physical captures and are useful
for parser and replay tests. They are not a universal register map:

| Frame | Evidence | Meaning established here |
| --- | --- | --- |
| `01 03 00 00 00 7d 85 eb` | `PROVEN_LIVE` | FC03 request for one observed native holding block. The block's device-specific semantics belong to the register authority. |
| `01 04 0b b8 00 7d b2 2a` | `PROVEN_LIVE` | FC04 request for one observed native input block. |
| `01 20 00 00 00 64 81 e6` | `PROVEN_LIVE` | FC20 request for 100 words. The broker forwards it on demand; this project does not assign universal word semantics. |
| `01 20 00 03 00 2b f5 d3` | `REPLAYED` | Discovery-related FC20 request found in the historical capture set. Treat the payload and response as device-specific evidence. |

The broker validates framing and CRC, associates responses with queued
requests, and exposes exceptions rather than turning them into successful
reads. Cache mode refreshes a complete native block on a miss. FC06/FC10
writes mark overlapping blocks stale and return the physical write response
immediately; the next cache refresh makes later reads coherent.

## Shine and firmware evidence

- The old ShineWiFi-X observations are historical and remain in
  [`archive/HA-DEV-2C_SHINE_TRANSPORT.md`](archive/HA-DEV-2C_SHINE_TRANSPORT.md)
  and the related 2D/3B reports.
- The ShineWiLan-X2 observations from 2026-09-25 are retained as historical
  evidence outside the normal runtime configuration. They do not prove that
  every cache profile, inverter model, or firmware version has identical
  timing.

The current reports do not prove the semantic identity of FC20 words, H188,
H209, VPP registers, asynchronous frames, or discovery values across inverter
families. Do not move those interpretations into PiITM code.

The bounded RPi canary is recorded in [PIITM_LIVE_ACCEPTANCE_20260925.md](PIITM_LIVE_ACCEPTANCE_20260925.md).

## Reproducible evidence

Compact fixtures live under `docs/data/` and are intentionally small. The
analysis tools can regenerate summaries from a capture supplied outside the
repository:

```bash
python tools/compact_capture.py --help
```

Do not commit credentials, private IP addresses, full production logs, or raw
firmware images. A new fixture should include its source, timestamp, hardware
profile, SHA-256, and evidence label.

## Handoff to Growatt_ModbusTCP

The useful handoff consists of:

1. PiITM topology, pacing, framing, ownership, and cache/write-through
   behavior from this repository;
2. the small request/response samples and raw-capture hashes above;
3. the exact inverter model and firmware under test; and
4. a link back to the Growatt_ModbusTCP or inverter-info record that assigns
   register names, scaling, access flags, or VPP meaning.

This separation lets protocol maintainers replay and inspect the transport
without treating a single installation's inferred map as a vendor-wide
contract.
