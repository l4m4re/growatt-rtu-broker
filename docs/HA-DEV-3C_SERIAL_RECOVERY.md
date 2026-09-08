# HA-DEV-3C serial recovery and cache gateway

## Problem addressed

Unplugging or plugging the ShineWiFi adapter can disturb the separate inverter
USB-RS485 adapter. Linux may then enumerate the inverter adapter as a new
`ttyUSB` number. A broker that keeps `/dev/ttyUSB0`, or a Docker device mapping
to a fixed minor number, can continue running while every inverter request
times out.

The broker now treats repeated inverter time-outs as a transport recovery
signal. After two consecutive unanswered physical attempts it:

1. closes the current serial handle;
2. reopens the stable paths captured at startup, preferring matching
   `/dev/serial/by-id` or `/dev/serial/by-path` aliases;
3. clears the RTU framer and operating-system input/output buffers;
4. retries the next eligible request and emits structured reopen events.

The event stream includes `inverter_serial_reopen`, `inverter_open_failed`,
`inverter_serial_reopened`, and `inverter_buffer_reset_failed`. A successful
Modbus response resets the consecutive-timeout counter. This is a local
transport recovery only; it does not power-cycle the inverter.

## Container requirement

Reopening can only work if the new device node is visible inside the broker's
namespace. A Docker `--device` mapping to `/dev/inverter` is bound to the old
device node and cannot follow a host-side USB re-enumeration by itself.

For a deployment where Shine or the inverter may be physically replugged, use
the stable host aliases in `.env` and enable the hot-plug deployment:

```ini
INV_DEV=/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
SHINE_DEV=/dev/serial/by-id/usb-04e2_1410-if00-port0
HOTPLUG_DEVICES=1
```

`docker/run_broker.sh` and `docker-compose.yml` then expose the host `/dev`
tree and pass the stable aliases to the broker. The process can observe the
updated symlink target and reopen the newly enumerated tty. This uses
`--privileged`/a `/dev` bind and is intentionally an operational trade-off;
an equivalent udev-aware host supervisor with a narrower device policy is
preferable where available.

The currently running no-Shine production baseline was restored by recreating
the stale container mapping after the inverter adapter returned as a new
`ttyUSB` minor. It should not be switched to cache mode until an explicit
canary is reviewed. The cache mode is opt-in:

```text
--mode legacy       # current rollback/default
--mode cache        # shared cache, no Shine serial
--mode cache+shine  # shared cache plus virtual Shine endpoint
```

## Recovery boundary

This mechanism can recover a serial close/reopen, stale input bytes, and a
host-visible USB re-enumeration. It cannot recover a device that is absent
from the container namespace, a failed USB-RS485 adapter, or an inverter that
has latched up internally. In those cases the broker continues retrying and
logs the open failures; an inverter power cycle remains the last resort.
