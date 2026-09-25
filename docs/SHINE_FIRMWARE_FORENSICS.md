# Shine firmware forensics

This runbook describes one bounded raw-transparent capture of Shine traffic.
It is a diagnostic profile for the PiITM service, not a normal deployment
mode. It applies to the ShineWiLan-X2 reference hardware tested on
2026-09-25 and can also be used for an older Shine adapter when its serial
settings are recorded separately.

## Safety boundary

The profile opens the inverter and Shine serial devices as the only transport
owner and forwards every byte in both directions. It does not parse Modbus,
retry, cache, synthesize responses, or expose Modbus TCP on ports 5020 or
5021. The optional port 5700 stream is observation only.

Stop the normal broker before starting the forensic profile. Never run both
profiles against the same serial devices. A capture must have a time limit and
be stored outside Git unless a small sanitised sample is needed for a test.

## Prepare a capture

Use stable device aliases. Identical CH340 adapters do not have unique USB
serial numbers, so the X2 path must be selected with `/dev/serial/by-path`:

```ini
# .env.forensic (local only; do not commit)
INV_DEV=/dev/serial/by-path/<inverter-port>
SHINE_DEV=/dev/serial/by-path/<shine-x2-port>
INV_BAUD=115200
INV_BYTES=8N1
SHINE_BAUD=115200
SHINE_BYTES=8N1
SNIFF_BIND=0.0.0.0:5700
FORENSIC_FILE=shine-firmware-update.jsonl
FORENSIC_MAX_BYTES=100000000
FORENSIC_MAX_SECONDS=3600
```

The one-shot compose profile has no TCP command endpoint:

```bash
mkdir -p captures
docker compose --env-file .env.forensic \
  -f docker-compose.forensic.yml run --rm forensic
```

Start the firmware operation from the Shine interface and keep the process
running through the observed reset/failure point. Stop it afterwards with
`Ctrl-C` or:

```bash
docker compose --env-file .env.forensic \
  -f docker-compose.forensic.yml down
```

The JSONL file contains `shine_wire_raw` records for both directions,
including non-Modbus bootloader/debug bytes. The byte and time limits prevent
an accidental unbounded capture.

## Inspect and preserve evidence

Keep the full capture on the target Pi or another protected evidence store.
Summarise it locally without putting the raw file in this repository:

```bash
python3 tools/analyze_raw_wire.py --help
python3 tools/analyze_forensic_rx.py --help
python3 - <<'PY'
import json
from pathlib import Path

for line in Path("captures/shine-firmware-update.jsonl").open():
    event = json.loads(line)
    if event.get("event") == "shine_wire_raw":
        print(event["ts"], event["direction"], event["length"], event["hex"][:80])
PY
```

Interpret candidate firmware blocks only after checking direction, length,
timing, and surrounding acknowledgements. An arbitrary byte sequence is not
evidence of a Modbus function code. Add a compact fixture only with its
hardware profile, timestamp, source hash, and evidence label.

## Restore normal operation

After the experiment, stop the forensic container and start the known-good
normal image and command. Record the image tag/digest and the rollback command
in the experiment report. The raw X2 bridge test on 2026-09-25 forwarded
traffic for more than ten minutes without timeout, CRC, exception, or serial
reopen events; that proves the bridge boundary for that window, not universal
compatibility with every Shine or inverter firmware.
