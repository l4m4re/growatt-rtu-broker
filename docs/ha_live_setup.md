# Home Assistant live deployment profile

This guide captures the exact configuration used to run the live Growatt broker on a Home Assistant (HA) installation while
simultaneously serving Home Assistant, a ShineWiFi dongle, and a developer laptop.

## Goals

* Share one inverter between Home Assistant (Modbus TCP) and the physical ShineWiFi dongle.
* Offer a second Modbus TCP endpoint so that ad-hoc tools or a devcontainer-based HA instance can talk to the inverter without
disturbing the production HA system.
* Relay every Modbus RTU frame to a read-only TCP stream so that a laptop can sniff traffic in real time for debugging or
register discovery.
* Avoid persistent log files on the HA SSD while still giving live observability through the sniff stream.

## Serial wiring and parameters

Both serial legs (inverter and ShineWiFi) are configured at **115200 baud, 8 data bits, no parity, 1 stop bit (115k2 8N1)**. If
the dongle is unplugged the broker will automatically retry and begin forwarding requests again once it reappears.

You can discover the device paths from the HA OS shell with:

```bash
ls -l /dev/serial/by-path
```

Record the inverter and ShineWiFi entries and place them in the `.env` file described below.

## Compose configuration

1. **Choose a persistent directory for the broker repository.**

  When using the Advanced SSH & Web Terminal add-on, you are inside a Docker container. Only certain directories are mapped to persistent storage and will survive reboots or add-on restarts:
  - `/config` (recommended for configuration and custom components)
  - `/share` (recommended for shared scripts, brokers, and data)
  - `/addons`, `/backups`, `/media`, `/ssl` (other special purposes)

  **Do not use `/root` or `/mnt/data/supervisor/homeassistant` inside the SSH add-on for persistent files.**

  For example, to clone the broker persistently:

  ```bash
  cd /share
  # Or: cd /config
  git clone https://github.com/your-org-or-user/growatt-rtu-broker growatt-rtu-broker
  ```

  The broker path will then be `/share/growatt-rtu-broker` (or `/config/growatt-rtu-broker`).

  For more details, see: https://community.home-assistant.io/t/user-file-changes-lost-on-reboot/545757/2
2. Create an `.env` file in that directory with the following content (replace the serial device paths with the values from your
   system):

   ```ini
   INV_DEV=/dev/serial/by-path/pci-0000:01:00.0-usb-0:1:1.0-port0
   SHINE_DEV=/dev/serial/by-path/pci-0000:01:00.0-usb-0:2:1.0-port0
   BAUD=115200
   BYTES=8N1
   TCP_BIND=0.0.0.0:5020
   TCP_ALT_BIND=0.0.0.0:5021
   SNIFF_BIND=0.0.0.0:5700
   MIN_PERIOD=1.0
   RTIMEOUT=1.5
   LOG_PATH=-
   ```

   * `TCP_BIND` feeds the production Home Assistant instance.
   * `TCP_ALT_BIND` is reserved for your laptop or devcontainer tooling.
   * `SNIFF_BIND` exposes a JSON Lines feed of every RTU request/response pair.
   * `LOG_PATH=-` disables on-disk logging to protect the HA SSD; use the sniff feed instead when you need live visibility.

3. Launch the container from the same directory:

   ```bash
   docker compose up -d
   ```

The compose file runs in host networking mode, so the TCP ports above are reachable directly on the HA IP address.

## Using the ports

| Purpose                    | Port | Notes |
|----------------------------|------|-------|
| Home Assistant (primary)   | 5020 | Configure HA's Modbus integration to talk to the host IP on this port. |
| Laptop / dev tools         | 5021 | Use Modbus clients such as `mbpoll`, `pymodbus`, or a second HA instance. |
| Real-time sniffing (JSONL) | 5700 | Consume with `nc <ha-ip> 5700` or `socat - TCP:<ha-ip>:5700`. |

The sniff stream yields newline-delimited JSON events with timestamps, request metadata, and CRC status. Example:

```json
{"ts":"2024-04-05T09:30:12.401","role":"REQ","from_client":"TCP:192.168.1.50:55032","uid":1,"func":4,"len":4,"addr":0,"count":2,"crc_ok":true,"hex":"01040000000271cb"}
{"ts":"2024-04-05T09:30:12.536","role":"RSP","to_client":"TCP:192.168.1.50:55032","uid":1,"func":4,"len":5,"crc_ok":true,"hex":"01040400000000d0f3"}
```

## Operational notes

* The ShineWiFi thread automatically retries if the dongle is unplugged and announces the state transitions on the sniff stream.
* All Modbus masters (HA, ShineWiFi, laptop) share a single downstream connection to the inverter. Transactions are serialized
  with `--min-period 1.0` to match the inverter timing requirements.
* Because logs are disabled, use the sniff stream for incident response. You can run `nc -k <ha-ip> 5700` on your laptop to keep
  a rolling view of live traffic.

Stop the container with `docker compose down` when maintenance is required. Update the `.env` file and rerun `docker compose up
-d` if device paths change.
