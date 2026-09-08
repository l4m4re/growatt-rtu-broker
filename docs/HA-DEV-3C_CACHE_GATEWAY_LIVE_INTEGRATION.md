# HA-DEV-3C cache gateway live integration

Status: Stage A accepted; Stage B is in progress with the already-connected
Shine. The current canary is not yet marked green because the inverter still
shows intermittent no-response transactions.

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

The Stage-A candidate was transferred from the exact local source commit
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
Stage-A containers remain stopped as rollback evidence.

## Stage-B transition

No Shine serial was opened by Stage A, and no Shine request or write was
forwarded. The user confirmed that the original Shine remained physically
plugged in throughout Stage A, so no additional insertion was needed.

The next controlled transition is to stop Stage A and start the same image in
`cache+shine` mode with this separate Shine path:

```text
/dev/serial/by-id/usb-04e2_1410-if00-port0
```

The first Stage-B image used `cache+shine` with separate stable inverter and
Shine paths. The current image includes the following corrections:

* every valid Shine frame is eligible for physical passthrough on a cache miss,
  including FC06/FC10 writes and unknown functions;
* cache hits are local optimizations, not policy blocks;
* valid inverter frames that do not match the current standard or FC20 response
  are recorded as asynchronous traffic and forwarded to the Shine;
* unplug/replug and serial-write errors close the Shine descriptor so the
  stable by-id path can be reopened automatically;
* an independent background poller is disabled in `cache+shine`, leaving Shine
  timing primary while HA cache misses still refresh the physical cache.

The current source commit is `b2e33dc995a356009b2b1734507a8022a13fef69`,
built on the RPi as:

```text
tag:      growatt-rtu-broker:ha-dev-3c-b2e33dc
image ID: sha256:689a7a6e27d7905a33d5218170bdcb79607a4b2e820547be5f4197c8d46ab647
container: growatt-broker-ha3c-stage-b-r4
mode:     cache+shine
paths:    inverter and Shine stable /dev/serial/by-id aliases
```

In the first ten minutes after this deployment, the broker observed 58 Shine
requests and 58 Shine responses, including successful physical Shine FC20
refreshes. Shine FC06 at H188 was forwarded physically and the identical
response was returned. There were 4 downstream time-outs and 3 automatic
inverter serial reopens in that window; these were not policy blocks. No
`async_frame_observed` event occurred in the bounded window, so absence of such
an event is not evidence that the inverter never emits asynchronous traffic.

The fresh primary sniff capture was:

```text
/tmp/growatt-ha-dev-3c-sniff-20260908-193259.jsonl
```

The existing `analyze_sniff_log.py` reported 2 Shine requests and 2 responses
in the portion captured, with zero time-outs, drops, CRC failures, or combined
frame suspects. The capture did not contain an asynchronous frame.
