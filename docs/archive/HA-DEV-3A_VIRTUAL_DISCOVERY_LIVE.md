# HA-DEV-3A virtual Shine discovery proof

Date: 2026-09-08 UTC
Production broker: `growatt-broker-ha2c-dfb99a8`
Production image: `growatt-rtu-broker:ha-dev-2b-dfb99a8`

## Safety boundary

The production broker remained running and was not restarted. The test opened
only the Shine adapter:

```text
/dev/serial/by-id/usb-04e2_1410-if00-port0
```

The physical inverter adapter was not opened, `/dev/inverter` was not mounted,
and no bytes were sent to the inverter. The temporary container used
`--network none` and mounted only the Shine device. No FC06 or FC10 was
observed or generated.

## Persistent capture proof

The Docker daemon on the live Home Assistant host uses a separate filesystem
namespace, so a normal `/tmp` bind mount is not host-visible. A unique Docker
volume was used instead:

```text
ha-dev-3a-capture-20260908
```

A preflight record was written by one container, that container was removed,
and a second container read the record successfully. The actual capture then
survived removal of the live test container and was read back separately.

Capture object:

```text
/capture/virtual-discovery-3.jsonl
bytes: 4244
lines: 22
sha256: 3b5c029cc8ebea3caaf2345356df4234be13d296f7cb2c16fd8cad2f165cbaf9
```

The raw capture remains outside Git in the Docker volume.

## Discovery baseline

The first five requests were complete 8-byte chunks, all exactly:

```text
00 03 00 2b 00 01 f5 d3
```

They arrived at approximately two-second intervals:

| ordinal | UTC | monotonic ms from start |
|---:|---|---:|
| 1 | 09:48:37.571 | 430.462 |
| 2 | 09:48:39.585 | 2444.448 |
| 3 | 09:48:41.619 | 4478.525 |
| 4 | 09:48:43.631 | 6490.910 |
| 5 | 09:48:45.575 | 8434.274 |

The measured request intervals were approximately 2.014, 2.034, 2.012,
and 1.943 seconds. No response was sent during this baseline.

## Controlled virtual response

On the sixth exact discovery request at 09:48:47.587 UTC, the harness wrote
exactly one complete response to the Shine adapter:

```text
01 03 02 13 ec b4 f9
```

The serial write returned `7`, equal to the response length. The serial output
was flushed 3.8 ms after the write completed. The request, write, return value,
and flush completion are all present in the JSONL record.

## Result

The Shine left discovery and began normal unit-1 polling without any physical
inverter response. The first three observed requests were:

```text
01 03 00 d1 00 0f 55 f7   FC03 H209 count 15
01 03 00 b4 00 14 05 e3   FC03 H180 count 20
01 03 00 00 00 7d 85 eb   FC03 H0 count 125
```

The first normal request arrived at 09:48:49.905 UTC, approximately 2.31 s
after the synthetic response flush. No FC06 or FC10 occurred before the test
stopped after three normal requests.

## Classification

```text
VIRTUAL_DISCOVERY_PROVEN
```

This proves the local bootstrap behavior for the validated live
`MIN 6000TL-XH` profile. It does not establish that `0x13ec` is a universal
Growatt constant; the response remains device-scoped in the prototype.
