# Growatt Pi in the Middle (PiITM)

PiITM is a small Raspberry Pi service that owns one Growatt inverter's
physical RS-485 connection. It gives Home Assistant, a Shine data logger, and
development tools controlled access to the same inverter while preserving
Modbus pacing, framing, and evidence. The project is independent of Home
Assistant and of the Growatt register authority.

The public package and command are still named `growatt-rtu-broker` and
`growatt-broker`. The PiITM name describes the deployed role.

## What is implemented

- one physical RTU owner with queued sources;
- Modbus TCP listeners for production and development clients;
- optional shared FC03/FC04 register-block cache;
- optional virtual or direct Shine path;
- bounded raw-transparent serial bridge for forensic captures;
- structured JSONL traffic and forensic logs;
- a dataset-backed simulator and analysis tools;
- FC06/FC10 write invalidation and complete-block readback in cache mode.

The cache is a register-block cache. It is not a duplicate TCP response cache.
A successful write is reported only after the affected block has been read back
through the same paced physical path. Register names, scaling, access flags,
and inverter-family semantics belong in
[Growatt inverter info](https://github.com/l4m4re/growatt-inverter-info) or
[Growatt_ModbusTCP](https://github.com/l4m4re/Growatt_ModbusTCP).

## Runtime modes

| Mode | Use |
| --- | --- |
| `legacy` | Physical inverter path, with optional direct Shine forwarding. |
| `cache` | Physical inverter owner and shared native FC03/FC04 cache. |
| `cache+shine` | Shared cache with a virtual Shine client path. |
| `cache+shine-direct` | Shared scheduler with direct Shine forwarding. |
| `cache+shine-predictive` | Shared cache plus observed Shine cadence and prefetch. |
| `legacy --shine-policy raw-transparent` | Temporary byte-for-byte forensic bridge. It disables both TCP listeners and must be the only owner of both serial ports. |

On the 2026-09-25 reference Pi, the normal X2 profile used 115200 8N1 on
both ports, a 0.5 second minimum transaction period, an 8 second RTU
timeout, a 6 second FC20 timeout, and an eight-transaction Shine burst. These
values are deployment evidence, not universal device requirements.

The ShineWiLan-X2 and inverter adapters are both CH340-class devices without
unique USB serial numbers. Select them with stable `/dev/serial/by-path`
aliases. Do not use `ttyUSB0` or `ttyUSB1` in a deployment guide.

## Install and local checks

From this repository:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,test]'
.venv/bin/ruff check .
.venv/bin/black --check .
.venv/bin/python -m pytest -q
```

The test suite is standalone; it does not require the Home Assistant pytest
plugins. CI runs the same checks on Python 3.11 and 3.14 and builds the Docker
image.

The installed `growatt-broker` command is the live broker. The older
dataset-demo CLI remains available as a library module for simulator tests:

```bash
python -m growatt_broker.cli run --mode dataset \
  --dataset growatt_broker/simulator/holding_tl_xh.json --duration 5
```

Use `growatt-broker --help` for the live command's complete option list.

## Installation configurations and setup mode

An installation configuration is one reviewable JSON file containing the
inverter/logger identification, serial settings, and complete native poll
plan. Example configurations are in
[`configs/examples/`](configs/examples/). The old ShineWiFi-X and current
ShineWiLan-X2 examples deliberately omit device serial numbers. The X2
candidate is write-disabled/read-only while it is in setup mode; the old
reviewed profile demonstrates the explicit enabled/transparent settings.

The same file controls write policy for the production TCP listener, the
development TCP listener, and Shine. Use `"disabled"` for either TCP value,
or `"read-only"` for Shine, when setup or a canary must not forward writes.
The older Docker environment variables (`PROD_TCP_WRITES`,
`DEV_TCP_WRITES`, and `SHINE_POLICY`) remain command-line overrides for
deployments that have not migrated to an installation file.

Live mode uses the approved plan without changing it:

```bash
growatt-broker --config /share/growatt-broker/installation.json
```

Setup mode observes Shine and TCP reads, adds stable unknown register blocks
to the in-memory cache plan, and keeps the original configuration unchanged.
The X2 example intentionally starts with an empty `poll_plan`; FC03, FC04,
and FC20 blocks are learned from the actual Shine traffic. Send `SIGUSR2` to
export the candidate configuration:

```bash
growatt-broker \
  --config /share/growatt-broker/installation.json \
  --operation-mode setup \
  --setup-export /share/growatt-broker/installation.candidate.json
kill -USR2 <broker-pid>
```

Only a reviewed candidate should be promoted to live mode. Setup mode does
not initiate background writes and does not replace the configured profile
automatically. Client-originated writes remain controlled by the configured
`write_policy` (set it to disabled/read-only for a write-free observation run).

The learned live profile is recorded in
[`configs/examples/growatt-min6000tl-xh-shinewilan-x2-learned.json`](configs/examples/growatt-min6000tl-xh-shinewilan-x2-learned.json).
It contains the twenty observed FC03/FC04/FC20 blocks for the current
MIN 6000TL-XH firmware (`ALBA18010122`) and ShineWiLan-X2 firmware
(`7.6.2.5`), with the FC03 `192/1` subset covered by the configured
`180/20` block. Predictive prefetch follows the observed approximately
twenty-second Shine sequence. The separate background refresh is deliberately
conservative at five minutes and acts as a fallback when Shine traffic is
absent; it does not set the normal Shine refresh cadence. The live RPi
currently uses this profile with production TCP, development TCP, and Shine
writes enabled.

The helper can mount the configuration on the RPi and write the candidate to a
separate host path:

```bash
CONFIG_PATH=/share/growatt-rtu-broker/configs/examples/growatt-min6000tl-xh-shinewilan-x2-current.json \
OPERATION_MODE=setup \
SETUP_EXPORT_PATH=/share/growatt-broker-x2.candidate.json \
docker/run_broker.sh
```

Review the exported file before changing `OPERATION_MODE` to `live`.

## Local simulator

The simulator serves a small deterministic register dataset over a pseudo
serial link for tests and experiments:

```bash
python -m growatt_broker.simulator.modbus_simulator --help
```

See [docs/SIMULATOR.md](docs/SIMULATOR.md). The simulator is a PiITM test
fixture; it is not a Home Assistant runtime dependency.

## Hardware deployment

Copy `.env.example` to `.env` and replace the device aliases with the aliases
on the target Pi:

```ini
INV_DEV=/dev/serial/by-path/<inverter-port>
SHINE_DEV=/dev/serial/by-path/<shine-x2-port>
BROKER_MODE=cache+shine-predictive
INV_BAUD=115200
INV_BYTES=8N1
SHINE_BAUD=115200
SHINE_BYTES=8N1
MIN_PERIOD=0.5
RTIMEOUT=8.0
FC20_TIMEOUT=6.0
SHINE_BURST=8
TCP_BIND=0.0.0.0:5020
TCP_ALT_BIND=0.0.0.0:5021
SNIFF_BIND=0.0.0.0:5700
PROD_TCP_WRITES=enabled
DEV_TCP_WRITES=enabled
LOG_PATH=-
HOTPLUG_DEVICES=1
```

For the combined Shine profile:

```bash
docker compose up -d --build
```

`docker-compose.yml` requires `SHINE_DEV`. For a no-Shine `legacy` or `cache`
deployment use `docker/run_broker.sh` or invoke the installed command
directly. The helper mounts the host `/dev` tree when `HOTPLUG_DEVICES=1`, so
a USB re-enumeration can be recovered without recreating the container.

The production listener is normally port 5020. Port 5021 is for development
tools and port 5700 streams JSONL sniff events. Bind these ports to the LAN
only when that is acceptable; the protocol has no built-in authentication or
encryption.

See [docs/ha_live_setup.md](docs/ha_live_setup.md) for deployment and
rollback. The normal rollback is to stop the new container and start the
previously tagged image with the previous command and device aliases.

## Forensic captures

Use [docs/SHINE_FIRMWARE_FORENSICS.md](docs/SHINE_FIRMWARE_FORENSICS.md) for
the raw-transparent profile. It is a bounded diagnostic mode, not a normal
broker mode:

```bash
mkdir -p captures
INV_DEV=/dev/serial/by-path/<inverter-port> \
SHINE_DEV=/dev/serial/by-path/<shine-x2-port> \
docker compose -f docker-compose.forensic.yml run --rm forensic
```

The profile has no Modbus TCP endpoint and must not run concurrently with the
normal broker. Store captures outside the repository unless a small,
sanitisable, reproducible sample is needed for a test. Never commit
credentials, private network addresses, or unbounded live logs.

## Evidence and protocol boundaries

[docs/PIITM_PROTOCOL_AND_EVIDENCE.md](docs/PIITM_PROTOCOL_AND_EVIDENCE.md) and the
[2026-09-25 live acceptance record](docs/PIITM_LIVE_ACCEPTANCE_20260925.md)
index compact samples and the evidence labels used by this project:
`PROVEN_LIVE`, `REPLAYED`, `STATIC_ANALYSIS`, `VENDOR_REFERENCE`,
`HYPOTHESIS`, and `NOT_TESTED`. The historical HA-DEV reports are retained
under [docs/archive](docs/archive) and are not current deployment
instructions.

The PiITM project proves transport and ownership behaviour. It does not claim
that FC20, VPP registers, discovery words, or H188 have universal semantics.
Those claims require device-specific evidence and belong with the register
authority.

## Development

Keep PiITM transport, capture, simulator, and deployment work in this
repository. Keep Home Assistant entities and automations in their integration
repository. Keep register definitions and device-specific interpretation in
the Growatt information repositories. Small reviewable changes are preferred;
include the command and evidence location for any live hardware test.

See [docs/ROADMAP.md](docs/ROADMAP.md) for the release plan,
[GROWATT_MODBUS_TCP_HANDOFF.md](docs/GROWATT_MODBUS_TCP_HANDOFF.md) for the
protocol handoff, and [AGENTS.md](AGENTS.md) for repository working rules.

MIT licensed.
