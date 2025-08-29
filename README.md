# Growatt RTU Broker

A tiny broker that arbitrates a single Modbus/RTU (RS‑485) bus between:
- Downstream: the inverter (single master)
- Upstream: the original ShineWiFi‑X (serial) and a Modbus‑TCP server for tools/HA

It enforces a minimum spacing between requests, proxies Shine traffic, exposes Modbus‑TCP for Home Assistant or pymodbus tools, and logs traffic as JSONL.

## Features
- RS‑485 master with request pacing (default 1.0s)
- ShineWiFi‑X passthrough on its serial port
- Modbus‑TCP server (default 0.0.0.0:5020)
- JSONL wire log with parsed fields and raw hex
- Simple, dependency‑light (pyserial only)

## Wiring and Devices
Use stable device paths under `/dev/serial/by-path` when identical USB‑RS485 adapters (e.g., CH340) lack unique IDs.

- Inverter adapter → connect to RS‑485 A/B of the inverter
- ShineWiFi‑X adapter → connect to Shine’s serial (if needed)

Serial defaults: 9600 8E1. Adjust if your installation differs.

## Install & Run

### Bare metal (Linux)
- Python 3.10+
- Install:
  - `pip install .` from the `growatt-rtu-broker` folder
- Run:
  - `growatt-broker --inverter /dev/serial/by-path/… --shine /dev/serial/by-path/… --baud 9600 --bytes 8E1 --tcp 0.0.0.0:5020`

### Docker
- Build and run with compose:
  - Edit `docker-compose.yml` and replace `<inverter-path>` and `<shine-path>` with your actual by‑path entries
  - `docker compose up -d`

### Parameters
- `--inverter` RS‑485 device to inverter (required)
- `--shine` RS‑485/serial device for Shine (required)
- `--baud` default baud when side‑specific not set
- `--bytes` default serial format, e.g. 8E1
- `--inv-baud`/`--inv-bytes` override for inverter side
- `--shine-baud`/`--shine-bytes` override for Shine side
- `--tcp` bind host:port for Modbus‑TCP (default 0.0.0.0:5020)
- `--min-period` minimum seconds between RTU transactions (default 1.0)
- `--rtimeout` read timeout for RTU response (default 1.5)
- `--log` path to JSONL log (default /var/log/growatt_broker.jsonl)

## Home Assistant
Point integrations that support Modbus‑TCP (e.g., our custom integration or native Modbus) to the broker host and TCP port.

## Log Format
JSONL with fields like:
- `ts`, `role` (REQ/RSP), `uid`, `func`, `addr`, `count`, `value`, `bytes`, `crc_ok`, `hex`, `from_client`/`to_client`

## Notes
- The broker is not a full Modbus core; it simply forwards and paces RTU frames.
- If RFC2217 gateways are desired, use a serial‑to‑TCP adapter on the client side; the broker itself uses local serial for reliability.

## License
MIT