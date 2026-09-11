# HA-DEV-3E broker consolidation review

Date: 2026-09-11
Repository: `l4m4re/Homeassistant-Growatt-Local-Modbus`
Branch: `fix/ha-dev-2b-forensics-20260906`
Source review head at start: `0c698e92c92b44e9c7406da3c5dcb534fd4b63f6`

## Outcome

The broker is accepted as the current HA development baseline:

```text
BROKER_BASELINE_ACCEPTED_FOR_HA_DEVELOPMENT
```

No showstopper was found in the single-owner scheduler, transparent Shine
path, cache gateway, serial recovery, or framing logic. The live canary was
stable during this review and was deliberately not restarted, rebuilt, or
reconfigured. This review therefore publishes source, tests, and operational
documentation without changing the running production container.

The branch was already equal to its remote before this review. The final
commit records only the intended helper, documentation, and test changes. The
untracked `broker-210925.log` and `broker-260925-2.log` files remain local and
are not part of the publication.

## Live baseline left running

The live Raspberry Pi deployment was inspected read-only. At inspection time
the relevant container was:

```text
container: growatt-broker-ha3d-cache-shine
image:     growatt-rtu-broker:ha-dev-3d-predictive-20260911c
status:    running
restarts:  0
mode:      cache+shine-predictive
```

Its configured interfaces were:

```text
inverter: /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
shine:    /dev/serial/by-id/usb-04e2_1410-if00-port0
baud:     115200
format:   8N1
TCP:      0.0.0.0:5020
TCP-alt:  0.0.0.0:5021
sniff:    0.0.0.0:5700
period:   1.0 s
timeout:  4.0 s
mode:     cache+shine-predictive
```

The observed log included successful FC20 physical refresh and a fresh virtual
Shine result. No live deployment action was needed to resolve a showstopper;
the stable running instance remains the operational reference until a later,
explicitly approved image rollout.

## Hardware topology and naming correction

The Pi-visible inverter transport is a USB/RS485 tunnel:

```text
RPi CH340 USB↔RS485
        ↕ RS485
inverter-side CH340 USB↔RS485
        ↕ USB
Growatt inverter USB host port
```

Only the RPi-side tunnel converter is a Pi-visible serial device. The
inverter's built-in RS485 buses are used by the BMS and the CHINT DDSU666
meter. The stock ShineWiFi-X is a separate Exar/XR21V1410-type USB serial
device (`04e2:1410`), not the second CH340 in the inverter tunnel. Stable
`/dev/serial/by-id` paths, rather than `ttyUSB` numbers, are consequently part
of the deployment contract.

## Reviewed runtime contracts

`Downstream` is the sole owner of the physical inverter serial port. It
serializes all physical transactions, applies the configured inter-command
period, gives Shine requests priority at transaction boundaries, and limits a
Shine burst so production/development clients are not permanently starved.

The current modes are:

| Mode | Shine requirement | Behaviour |
| --- | --- | --- |
| `legacy` | optional | Existing physical/legacy path; no cache gateway required |
| `cache` | none | Cache-backed FC03/FC04 TCP service for HA/tools |
| `cache+shine` | required | Cache-backed Shine reads with physical fallback |
| `cache+shine-direct` | required | Cache available to TCP clients; Shine uses transparent framed path |
| `cache+shine-predictive` | required | Cache gateway plus cadence-based Shine prefetch |

The physical Shine path remains transparent where the cache policy does not
apply. Shine reads, writes, FC20, discovery, and unknown profiled traffic can
reach the inverter and responses are returned to Shine. TCP clients receive
cache-backed FC03/FC04 reads; general TCP writes are intentionally not exposed
yet. This preserves the safety boundary that only the reviewed physical Shine
path may currently originate writes.

The serial framer can receive multiple frames in one operating-system read,
but it separates complete RTU frames before dispatch. A matched response is
returned to its requester, while valid unmatched/asynchronous frames are
reported and forwarded to Shine when the Shine endpoint is present. Outbound
requests are written as individual frames; two input frames are not blindly
concatenated and treated as one Modbus transaction.

## Recovery and failure assessment

Repeated standard-read timeouts trigger a serial close/reopen attempt using
the stable path candidates, followed by framer and OS input/output buffer
clearing. USB re-enumeration is supported when the container can see `/dev`.
A single Shine timeout does not itself force an inverter reopen, and an
inverter power cycle remains a last-resort physical operation.

The remaining failure class is genuine inverter no-response, especially around
opaque FC20 traffic and asynchronous inverter frames. The broker now records
request source, response association, timeout/retry, asynchronous frames,
unframed bytes, reopen events, and cache/FC20 decisions on the sniff stream.
The evidence is sufficient for continued diagnosis, but not yet for a causal
claim about every late or missing FC20 response.

## Changes made in this consolidation

- Made `docker/run_broker.sh` represent the CLI contract: Shine is optional in
  `legacy` and `cache`, while `cache+shine*` fails early without `SHINE_DEV`.
  Hot-plug mounting and conditional `--shine` handling remain intact.
- Corrected the hardware description in the live setup, Shine transport, and
  3D checkpoint documents.
- Corrected the portal-write notation to `FC0x10` (Write Multiple Registers).
- Updated the README, environment example, compose note, and reconciliation
  note so the deployment profiles and stable serial aliases agree.
- Added deterministic regression coverage for multiple asynchronous frames in
  one serial read, FC20 framing with preceding asynchronous frames, cache
  operation without a Shine client, and Shine priority over predictive work.

## Deliberately deferred

These are follow-up items, not blockers for the current HA handoff:

- General Modbus write support on `PROD_TCP` and `DEV_TCP`; this needs an
  explicit write policy, authorization, serialization, audit logging, and
  read-back design.
- Changing the running live image or restarting the stable canary.
- Full refactoring of the large `broker.py` module and repository-wide Ruff
  cleanup. The current Ruff result is 263 existing errors, mostly legacy
  hygiene issues; a broad cleanup would be unrelated risk.
- Definitive FC20 payload semantics, Shine backlog interpretation, and the
  undocumented asynchronous protocol. Existing forensic tools and captures
  are retained for that work.
- Further predictive scheduling and block-age tuning after the HA register
  map and client polling requirements are finalized.
- Any sensor/register migration in Home Assistant. Entity identity,
  statistical meaning, and cumulative counter continuity remain the HA-side
  compatibility contract.

No forensic tools, historical reports, captures, or local research artefacts
were deleted. No dead-code cleanup was attempted merely to make the review
look smaller.

## Verification and handoff

The source checks for this checkpoint are:

```text
bash -n docker/run_broker.sh                         PASS
python3 -m compileall -q growatt_broker tools tests  PASS
python3 -m pytest --asyncio-mode=auto                 PASS for the relevant suite
git diff --check                                     PASS
```

The repository-wide Ruff command remains non-zero because of the pre-existing
263-error backlog described above; no unrelated mass rewrite was included.

The next task can therefore return to the Home Assistant integration with
these assumptions:

1. the live broker remains a stable, separately monitored canary;
2. HA production uses TCP 5020 and development/HIL uses TCP 5021;
3. port 5700 is the primary bounded runtime evidence stream;
4. cache reads do not require the Shine device;
5. the Shine remains hot-pluggable and its physical traffic is observed and
   forwarded according to the selected mode;
6. no general TCP write contract exists yet; and
7. the broker is a transport/evidence service, while the HA task owns the
   semantic register map and entity compatibility decisions.

The next implementation should consume the validated family-specific read
plan and sensor continuity contract rather than rediscovering transport
ownership or timing constraints.
