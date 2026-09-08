# HA-DEV-3C cache gateway live integration

Status: Stage A accepted; proceeding to Stage B with the already-connected
Shine after user confirmation.

## Legacy baseline

Before the canary, the known-good production container was checked without
changing its image or configuration:

| Check | Result |
|---|---|
| physical inverter | valid FC04 response through the broker |
| broker `:5020` | valid 259-byte Modbus-TCP response |
| broker `:5021` | valid 259-byte Modbus-TCP response |
| production HA | repeated `growatt_local` fetches with `success: True` |
| recent HA errors | no `No response received` or `no_response` lines |
| legacy image | `growatt-rtu-broker:ha-dev-2b-dfb99a8` |
| legacy container | retained stopped as `growatt-broker-ha2c-dfb99a8` |

The recovery had previously required recreating a stale Docker device mapping
after the inverter adapter re-enumerated from one `ttyUSB` minor to another.
The Shine and inverter aliases were distinct:

```text
/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0  -> inverter
/dev/serial/by-id/usb-04e2_1410-if00-port0         -> Shine
```

## Candidate and runtime

The candidate was transferred from the exact local source commit
`643a512c984cadfa68937311268f45a3c23e2d93` and built on the RPi as:

```text
tag:      growatt-rtu-broker:ha-dev-3c-643a512
image ID: sha256:80d6e12352ad0807f55fc5eccac49cd0b00dc74cc9c9a8b761321d5253d2cb5a
mode:     cache
inverter: /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
baud:     115200 8N1
ports:    5020, 5021, 5700
period:   1.0 s
timeout:  1.5 s
Shine:    physically connected, but intentionally not opened or mapped in Stage A
```

The candidate was the only physical inverter serial owner. Its container used
a host `/dev` bind and the stable inverter alias; no Docker `--device` mapping
to a fixed tty minor was used.

## Stage-A result: HA plus physical inverter, no Shine client

The first candidate run exposed a polling bug: a due background refresh was
mistakenly served from the still-fresh cache entry, producing a rapid cache-hit
loop. It was immediately stopped and legacy was restored. The corrected
candidate uses an explicit forced refresh for due background entries.

The retry canary then ran long enough to cover multiple normal HA cycles:

| Observation window | Result |
|---|---|
| physical block refreshes | 47 |
| cache hits | 113 |
| coalesced demands | 2 |
| physical refresh failures | 0 |
| downstream time-outs | 0 |
| inverter serial reopen events | 0 |
| HA fetches observed | successful; `6.395 s`, `6.390 s`, then `0.073 s`, `3.277 s`, `0.930 s` |
| direct TCP checks | `:5020` and `:5021` both valid |

The sub-second HA fetches demonstrate that HA is being served from the cache,
while the physical log shows the native FC03/FC04 block refreshes continuing.
The two coalesced events show that overlapping background and client demand
does not create duplicate physical transactions.

Stage A is therefore accepted for progression to the real Shine test. The
first failed candidate remains stopped as `growatt-broker-ha3c-stage-a`; the
accepted retry remains the active canary as `growatt-broker-ha3c-stage-a-r2`.

## Stage-B transition

No Shine serial was opened by Stage A, and no Shine request or write was
forwarded. The user confirmed that the original Shine remained physically
plugged in throughout Stage A, so no additional insertion is needed.

The next controlled transition is to stop Stage A and start the same image in
`cache+shine` mode with this separate Shine path:

```text
/dev/serial/by-id/usb-04e2_1410-if00-port0
```

After confirmation, the same candidate will be restarted in `cache+shine`
mode with separate stable inverter and Shine paths. Discovery will be answered
locally using the validated device-scoped response; standard reads will use the
common cache; FC20 will remain opaque and writes will remain quarantined.
