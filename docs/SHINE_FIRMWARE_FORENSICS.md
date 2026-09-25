# ShineWiFi inverter firmware forensics

This profile is for one bounded firmware-update observation. It is based on
the live broker commit tagged `live-broker-20260925` and uses the existing
`raw-transparent` serial bridge.

## Safety boundary

The profile does not expose Modbus TCP on ports 5020 or 5021. Home Assistant
and other TCP clients therefore cannot issue inverter requests during the
capture. It opens the inverter and Shine serial devices as the only transport
owner and forwards every byte in both directions without Modbus parsing,
timing, retries, caching, or response synthesis.

The optional sniff stream on port 5700 is an observation feed only. It does
not provide a Modbus command endpoint.

Stop the normal broker before starting this profile. Do not run both profiles
against the same serial devices.

## Start

From this repository, create a local `.env.forensic` file (do not commit it):

```ini
INV_DEV=/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
SHINE_DEV=/dev/serial/by-id/usb-04e2_1410-if00-port0
INV_BAUD=115200
INV_BYTES=8N1
SHINE_BAUD=115200
SHINE_BYTES=8N1
SNIFF_BIND=0.0.0.0:5700
FORENSIC_FILE=shine-firmware-update.jsonl
FORENSIC_MAX_BYTES=100000000
FORENSIC_MAX_SECONDS=3600
```

Create the local capture directory and start the one-shot container:

```bash
mkdir -p captures
docker compose --env-file .env.forensic \
  -f docker-compose.forensic.yml up --build
```

The raw JSONL capture is written to `captures/` on the host. It includes
`shine_wire_raw` records for both `shine_to_inverter` and
`inverter_to_shine`, including non-Modbus bootloader/debug bytes. The bounded
forensic limit prevents an accidental unbounded capture.

While the container is running, start the update from the ShineWiFi AP and
leave the capture running through the 25% failure/reset point. Stop it after
the reset with `Ctrl-C` or:

```bash
docker compose --env-file .env.forensic \
  -f docker-compose.forensic.yml down
```

## Analyse

Keep the capture outside Git. First inspect the raw directions and sizes:

```bash
python3 - <<'PY'
import json
from pathlib import Path

for line in Path("captures/shine-firmware-update.jsonl").open():
    event = json.loads(line)
    if event.get("event") == "shine_wire_raw":
        print(event["ts"], event["direction"], event["length"], event["hex"][:80])
PY
```

The existing `tools/analyze_raw_wire.py` and
`tools/analyze_forensic_rx.py` can then be used for structured summaries.
Interpret any candidate firmware block only after checking its direction,
length, timing, and surrounding bootloader acknowledgements. Do not infer a
Modbus function code from an arbitrary byte inside a raw block.

After the experiment, restore the normal broker from the live tag before
re-enabling Home Assistant access.
