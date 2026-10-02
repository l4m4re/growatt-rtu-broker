# Home Assistant live deployment

This guide describes the PiITM container on a Home Assistant OS Raspberry Pi.
The PiITM container owns the physical inverter serial connection and exposes
Modbus TCP to Home Assistant, a development instance, and optional Shine
transport. Keep the image tag, command, device paths, and rollback image in
the deployment notes for the target installation.

## Discover stable serial paths

The live installation has two CH340-class adapters without unique USB serial
numbers. Do not use `ttyUSB0` or `ttyUSB1`. On the HA host, list the stable
physical aliases and identify them by cable and port:

```bash
ls -l /dev/serial/by-path
ls -l /dev/serial/by-id
```

Use the inverter and ShineWiLan-X2 `/dev/serial/by-path/...` paths in `.env`.
The path is part of the hardware identity and can change if an adapter is
moved to another USB port.

## Install in persistent storage

From the Advanced SSH & Web Terminal add-on, use `login` to reach the HA host
where Docker is available. Keep the checkout under `/share` or `/config`:

```bash
cd /share
git clone https://github.com/l4m4re/growatt-rtu-broker.git growatt-rtu-broker
cd /share/growatt-rtu-broker
cp .env.example .env
```

Select one reviewed installation configuration, for example:

```ini
CONFIG_PATH=/share/growatt-rtu-broker/configs/examples/growatt-min6000tl-xh-shinewilan-x2.json
TCP_BIND=0.0.0.0:5020
TCP_ALT_BIND=0.0.0.0:5021
SNIFF_BIND=0.0.0.0:5700
LOG_PATH=-
HOTPLUG_DEVICES=1
```

The installation JSON is the source of truth for the inverter and Shine
paths, baud rates, serial formats, RTU timeout, reopen threshold, write
policies, mode, and poll plan. The helper does not override those values when
`CONFIG_PATH` is set. The RTU timeout and background polling cadence are
separate concepts.

For an installation whose Shine poll set is not yet known, use the X2 example
in setup mode. The reviewed X2 profile contains the complete FC03 and FC04
blocks learned from the physical Shine stream. FC20 is observed for diagnostics
but remains on demand:

```bash
CONFIG_PATH=/share/growatt-rtu-broker/configs/examples/growatt-min6000tl-xh-shinewilan-x2.json
OPERATION_MODE=setup
SETUP_EXPORT_PATH=/share/growatt-broker-x2.candidate.json
```

The `docker/run_broker.sh` helper mounts the source configuration read-only and
the export directory read-write. After a bounded observation window, send
`SIGUSR2` to the broker, review the candidate, and only then promote it to live
mode. During this process, set each client write policy to `disabled`.

The reviewed profile produced on 2026-09-26 is
`configs/examples/growatt-min6000tl-xh-shinewilan-x2.json`. It contains the observed FC03 and FC04 native blocks. FC20 is served on demand,
not background-polled. The read-only cache poller selects the oldest configured
block at a nine-second target age; production TCP, development TCP, and Shine
writes are enabled. The previous live and setup containers remain named rollback
containers on the Pi.

## Build and run

For the combined Shine profile:

```bash
cd /share/growatt-rtu-broker
docker compose up -d --build
docker logs -f growatt-rtu-broker
```

The compose file mounts `/dev`, uses host networking, and requires
`SHINE_DEV`. For a no-Shine `legacy` or `cache` profile, use the helper so the
Shine argument is omitted and USB hot-plug behavior is preserved:

```bash
/share/growatt-rtu-broker/docker/run_broker.sh
docker logs -f growatt-broker
```

The helper uses the same side-specific baud/format, timeout, Shine policy, and
write settings as the compose profile. It can mount the host
`/dev` tree when `HOTPLUG_DEVICES=1`, which allows a re-enumerated adapter to
be recovered without recreating the container.

The direct command is useful for a controlled no-Shine test:

```bash
docker run --rm --privileged -v /dev:/dev --network host \
  growatt-rtu-broker:local \
  growatt-broker --inverter "$INV_DEV" --mode cache \
  --inv-baud "${INV_BAUD:-115200}" --inv-bytes "${INV_BYTES:-8N1}" \
  --tcp "${TCP_BIND:-0.0.0.0:5020}" --tcp-alt "${TCP_ALT_BIND:-0.0.0.0:5021}" \
  --sniff "${SNIFF_BIND:-0.0.0.0:5700}" \
  --rtimeout "${RTIMEOUT:-0.9}" \
  --prod-tcp-writes "${PROD_TCP_WRITES:-enabled}" \
  --dev-tcp-writes "${DEV_TCP_WRITES:-enabled}" --log "${LOG_PATH:--}"
```

Do not use the direct command and the helper for the same container name at
the same time. The production listener is normally port 5020, the development
listener is 5021, and the JSONL sniff stream is 5700. These listeners have no
built-in authentication or encryption; restrict them to a trusted network.

## Read-only acceptance before writes

Before an approved write test, verify:

1. the container has one expected inverter owner and one expected Shine path;
2. HA can read representative input and holding blocks on port 5020;
3. a development client can read on port 5021;
4. Shine traffic is visible on port 5700 and no CRC/exception/reopen storm is
   present; and
5. the queue, physical latency, cache age, and timeout counters are recorded.

## Stop, update, and rollback

Keep the previous image tag or digest before an update. A normal rollback is:

```bash
docker compose down
docker tag growatt-rtu-broker:<known-good> growatt-rtu-broker:local
# restore the previous .env and command, then:
docker compose up -d
```

For the helper-managed container:

```bash
docker stop growatt-broker || true
docker rm growatt-broker || true
# restore the previous image and .env
docker run ...
```

Record the exact rollback command in the live test report. Do not change live
writes, firmware, or serial timing without a bounded test and an explicit
rollback.
