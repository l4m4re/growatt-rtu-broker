# PiITM live acceptance — 2026-09-25

Evidence label: `PROVEN_LIVE` for this installation and time window.
This is a bounded read-only canary, not a universal inverter or Shine
compatibility claim.

## Candidate

- source branch: `forensic/shine-firmware-update-20260925`
- source commit: `cb90817a49540084a4080a606e6fae8cb7307b62`
- image: `growatt-rtu-broker:piitm-audit-cb90817`
- image ID: `sha256:d37222ce729ec47caa57bd3245ded46ce8c5a1eab2ac934d109c48830dc606a8c`
- mode: `cache+shine-predictive`
- inverter and Shine: 115200 8N1
- minimum period: 0.5 seconds
- RTU timeout: 8 seconds
- FC20 timeout: 6 seconds
- Shine burst: 8
- production and development TCP writes: disabled
- Shine policy: `read-only`

The candidate was built on the RPi with the repository Dockerfile. The normal
container was stopped for the canary and retained unchanged for rollback.

## Read-only checks

The following checks were run while the candidate owned both serial devices:

```text
TCP 5020, holding start 0 count 2 -> [1, 401]
TCP 5021, holding start 0 count 2 -> [1, 401]
TCP 5700 -> valid JSONL cache/FC20 and Shine traffic
```

The candidate logs showed CRC-valid FC03, FC04, FC20, cache refresh, and
virtual-Shine response events. One Shine FC04 request timed out after the
configured eight seconds during the bounded window. The broker emitted the
physical timeout and empty response event; it did not fabricate a successful
read. This is consistent with the known intermittent physical no-response
behavior and remains an open transport reliability item.

No write request was sent by the acceptance commands, and the candidate's
TCP write policies were disabled.

## Rollback

The candidate was stopped and removed. The pre-existing container was started
again with its original configuration:

```text
container: growatt-broker-ha3d-cache-shine
image: growatt-rtu-broker:single-cache-fa3b694
restart policy: unless-stopped
mode: cache+shine-predictive
```

A post-rollback read on the development listener returned `[1, 401]` and the
container was running. The existing image remains the live rollback image;
no release candidate was left deployed.

## Interpretation

This proves that the current source can be built on the target RPi, run with
writes disabled, serve both TCP listeners, preserve Shine/cache traffic, and
be rolled back without changing the known-good container configuration. It
does not prove zero physical timeouts, write behavior, firmware-update
behavior, or register semantics across other Growatt installations.
