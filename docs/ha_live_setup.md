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
ls -l /dev/serial/by-id
```

Record the inverter and ShineWiFi entries (use the full `/dev/serial/by-id/...` paths) for the commands below.

## Deployment on Home Assistant (without Docker Compose)

From the Advanced SSH & Web Terminal add-on, run `login` first to drop into the HA host shell—`docker` lives there, not inside the add-on container.

1. Choose a persistent directory for the broker repository.

   When using the Advanced SSH & Web Terminal add-on, you are inside a Docker container. Only certain directories are mapped to persistent storage and will survive reboots or add-on restarts:
   - `/config` (recommended for configuration and custom components)
   - `/share` (recommended for shared scripts, brokers, and data)
   - `/addons`, `/backups`, `/media`, `/ssl` (other special purposes)

   Do not use `/root` or `/mnt/data/supervisor/homeassistant` inside the SSH add-on for persistent files.

   For example, to clone the broker persistently:

   ```bash
   cd /share
   # Or: cd /config
   git clone https://github.com/l4m4re/growatt-rtu-broker.git growatt-rtu-broker
   ```

   The broker path will then be `/share/growatt-rtu-broker` (or `/config/growatt-rtu-broker`).

2. Copy `.env.example` to `.env` and update the values for your hardware.

   ```bash
   cd /share/growatt-rtu-broker
   cp .env.example .env
   ```

   Edit `.env` with your preferred editor (`nano .env`, `vi .env`, etc.) and set at least the device paths recorded earlier:

   ```ini
   INV_DEV=/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
   SHINE_DEV=/dev/serial/by-id/usb-04e2_1410-if00-port0
   TCP_BIND=0.0.0.0:5020
   TCP_ALT_BIND=0.0.0.0:5021
   SNIFF_BIND=0.0.0.0:5700
   MIN_PERIOD=1.0
   RTIMEOUT=1.5
   LOG_PATH=-
   ```

   Optional overrides from `.env.example` allow you to tweak baud settings per leg; leave them commented unless you have a reason to change the defaults.

3. Build the image once so the local image exists:

   ```bash
   cd /share/growatt-rtu-broker
   docker build -t growatt-rtu-broker:local .
   ```

   Build notes:
   - You may see: `DEPRECATED: The legacy builder is deprecated... Install the buildx component...`.
     This warning can be ignored on HA OS; the image will still build and tag successfully.
   - On success you should see: `Successfully tagged growatt-rtu-broker:local`.


4. Start the container with your `.env` values.

   If you copied `.env.example` to `.env` and adjusted the values, you can reuse those settings when starting the container without Docker Compose.

   1. Load the variables into your current shell:

      ```bash
      set -a; . /share/growatt-rtu-broker/.env; set +a
      # If you cloned under /config, use that path instead
      # set -a; . /config/growatt-rtu-broker/.env; set +a
      ```

   2. Start the container, using the locally built image:

      You can start the container directly (this will fail if one of the `--device` paths
      does not exist), or use the included helper which omits missing devices so the
      broker can be started before the Shine dongle is plugged in.

      ```bash
      # Direct run (will fail if devices are missing)
      docker run -d \
        --name growatt-broker \
        --restart unless-stopped \
        --device="$INV_DEV":/dev/inverter:rw \
        --device="$SHINE_DEV":/dev/shine:rw \
        -p "${TCP_BIND##*:}":"${TCP_BIND##*:}" -p "${TCP_ALT_BIND##*:}":"${TCP_ALT_BIND##*:}" -p "${SNIFF_BIND##*:}":"${SNIFF_BIND##*:}" \
        growatt-rtu-broker:local \
        growatt-broker --inverter /dev/inverter --shine /dev/shine \
          --baud "${INV_BAUD:-${BAUD:-115200}}" --bytes "${INV_BYTES:-${BYTES:-8N1}}" \
          --tcp "${TCP_BIND:-0.0.0.0:5020}" --tcp-alt "${TCP_ALT_BIND:-0.0.0.0:5021}" --sniff "${SNIFF_BIND:-0.0.0.0:5700}" \
          --min-period "${MIN_PERIOD:-1.0}" --rtimeout "${RTIMEOUT:-1.5}" --log "${LOG_PATH:--}"
      ```

      ```bash
      # Use the helper; it will source /share/growatt-rtu-broker/.env and skip missing devices
  /share/growatt-rtu-broker/docker/run_broker.sh

  Note: if the helper detects a missing device path it will start the container in a
  hot-plug mode by adding `--privileged` and bind-mounting `/dev` into the container.
  This allows the broker to see devices that are plugged in after the container starts.
  Starting in privileged mode grants broad device access; prefer explicit `--device`
  flags when all devices are present.
      ```

   Notes:
   - `INV_DEV` and `SHINE_DEV` come from `.env` and must be valid host device paths (check with `ls -l "$INV_DEV" "$SHINE_DEV"`).
   - We source `.env` in the host shell so variables can be used for both host options (like `--device` and `-p`) and for the broker CLI flags.
   - If a container named `growatt-broker` already exists: `docker stop growatt-broker && docker rm growatt-broker`.
   - View logs: `docker logs -f growatt-broker`.


5. Verify and operate.

   - View logs:
     ```bash
     docker logs -f growatt-broker
     ```
   - Stop/remove:
     ```bash
     docker stop growatt-broker && docker rm growatt-broker
     ```
   - Update to latest source (Option A):
     ```bash
     cd /share/growatt-rtu-broker && git pull
     docker restart growatt-broker
     ```
   - Rebuild after changes (Option B):
     ```bash
     cd /share/growatt-rtu-broker
     docker build -t growatt-rtu-broker:local .
     docker stop growatt-broker && docker rm growatt-broker
     # re-run the docker run command from above
     ```



## Using the ports

| Purpose                    | Port | Notes |
|----------------------------|------|-------|
| Home Assistant (primary)   | 5020 | Configure HA's Modbus integration to talk to the host IP on this port. |
| Laptop / dev tools         | 5021 | Use Modbus clients such as `mbpoll`, `pymodbus`, or a second HA instance. |
| Real-time sniffing (JSONL) | 5700 | Consume with `nc <ha-ip> 5700` or `socat - TCP:<ha-ip>:5700`. |

The ports above are published directly on the HA host IP.

## Operational notes

* The ShineWiFi thread automatically retries if the dongle is unplugged and announces the state transitions on the sniff stream.
* All Modbus masters (HA, ShineWiFi, laptop) share a single downstream connection to the inverter. Transactions are serialized
  with `--min-period 1.0` to match the inverter timing requirements.
* Because on-disk logs are disabled (`--log -`), use `docker logs -f growatt-broker` or the sniff stream for incident response.
* If you expect to plug the Shine dongle in after the container starts, use `docker/run_broker.sh` which will
  start the container without binding the missing device; when the dongle is plugged in the broker will detect
  it and begin forwarding traffic.

---

Troubleshooting tips:
- If the `docker` command is not found inside the Advanced SSH add-on, ensure you typed `login` to drop to the host shell.
- If your serial devices are not found, confirm the by-id paths and that no other container is holding the device.
- If ports appear in use, check for existing containers bound to 5020/5021/5700 and stop them.
