# HA-DEV-3D session checkpoint — cache gateway, Shine observation, and next HA work

Date: 2026-09-11
Status: live canary still running; no source or live configuration rollback
performed in this checkpoint

This document records the broker work and live observations from the combined
HA, inverter, and Shine investigation. It is a checkpoint for the next phase:
complete the Home Assistant register map and use the broker as the controlled
live access path from the development container.

The report deliberately distinguishes three different states:

1. the known-good production broker without Shine;
2. the earlier raw-transparent forensic bridge; and
3. the current cache-centric predictive broker with the physical Shine
   connected.

They must not be treated as one interchangeable implementation.

## Current live deployment

At the time of this checkpoint the RPi was running:

```text
container: growatt-broker-ha3d-cache-shine
image:    growatt-rtu-broker:ha-dev-3d-predictive-20260911c
status:   running
restarts: 0
started:  2026-09-11T07:16:39Z
mode:     cache+shine-predictive
```

The effective command line is:

```text
growatt-broker \
  --inverter /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 \
  --shine /dev/serial/by-id/usb-04e2_1410-if00-port0 \
  --baud 115200 --bytes 8N1 \
  --tcp 0.0.0.0:5020 \
  --tcp-alt 0.0.0.0:5021 \
  --sniff 0.0.0.0:5700 \
  --min-period 1.0 --rtimeout 4.0 \
  --log - --mode cache+shine-predictive
```

The stable serial identities are more important than the current `ttyUSB`
minor numbers:

```text
inverter: /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
Shine:    /dev/serial/by-id/usb-04e2_1410-if00-port0
```

The ports are:

| Port | Purpose |
|---:|---|
| 5020 | production Home Assistant (`PROD_TCP`) |
| 5021 | development HA/tools (`DEV_TCP`) |
| 5700 | JSONL passive broker sniff/event stream |

The current live process therefore still has a `--shine` argument and is not
the clean no-Shine configuration. The source supports that configuration as
`--mode cache` with `--shine` omitted; the inverter serial remains required.
Changing to that mode requires a deliberate container restart and was not done
as part of this checkpoint.

The current source tree is intentionally dirty because it contains the active
research changes and local evidence. This report does not consume or reset
those changes.

## What was implemented and deployed

The broker evolved from a single legacy RTU/TCP path into a cache gateway with
one physical inverter owner:

```text
physical inverter serial
          |
   downstream coordinator
          |
   native block cache
      /          \
 HA TCP clients   virtual Shine path
```

The cache key is a physical contiguous range `(function, start, count)`, not
an HA entity. Complete native blocks are retained even when only some words
become useful semantic values. This preserves the transaction-count
optimization required by the Growatt bus.

The live predictive mode now:

- keeps one physical downstream transaction owner;
- serves standard FC03/FC04 reads from the shared cache when fresh;
- coalesces overlapping cache misses into one physical refresh;
- refreshes native blocks in the background and can use observed Shine cadence
  for predictive prefetch;
- treats FC20 as an opaque request/response object rather than inventing a
  register map;
- preserves the validated Shine discovery handling;
- forwards valid Shine writes and otherwise unsupported valid requests through
  the physical path according to the current Shine passthrough policy;
- records inverter-originated asynchronous frames and forwards them to the
  Shine when the physical endpoint is connected;
- detects serial disappearance and reopens the stable by-id path;
- exposes the separate sniff stream on TCP 5700.

The earlier raw-transparent bridge remains a diagnostic tool. It forwards
serial bytes/chunks immediately in both directions and does not apply Modbus
parsing, cache replies, scheduling, or retry logic. It is not the current
production architecture.

## Live experiments and observations

### No-Shine baseline and recovery

The known-good no-Shine broker was repeatedly used as the recovery baseline.
When Shine experiments caused an unhealthy or ambiguous Shine session, the
raw/combined candidate was stopped and HA was returned to the inverter-only
broker. Development HA then resumed valid unit-1 FC03/FC04 traffic.

This established two important facts:

- the inverter and production HA can operate reliably through the broker
  without Shine traffic;
- Shine failures must not be diagnosed as an HA register-map failure without
  first separating the physical Shine session from the HA TCP client path.

The no-Shine mode is therefore a supported target configuration, but it is not
what the current live predictive container is running.

### Shine hot-plug and startup behavior

The two CH340-class adapters can re-enumerate with different `ttyUSB` numbers.
The broker's stable by-id paths are consequently part of the operational
contract. During the raw-transparent experiment the Shine path disappeared
and returned while the broker stayed alive; the broker logged open failures,
closed the stale descriptor, and reopened the device after hot-plug without a
broker restart.

The stock Shine also emitted printable ESP8266 boot/debug data after restart,
followed by binary data. In raw-transparent mode those bytes were forwarded;
they were not treated as a Modbus response. After some resets the Shine
repeated the CRC-valid discovery probe:

```text
00 03 00 2b 00 01 f5 d3
```

The raw captures did not contain a response to every such probe. In other
captures the Shine progressed to normal unit-1 FC03/FC04/FC20 polling after
the known discovery response was observed. A Shine cloud connection alone is
not proof of a working local inverter session: the portal separately reported
the datalogger connected while the inverter/system status remained offline.

### Transparent forwarding and asynchronous traffic

The raw-transparent capture proved that the broker can preserve Shine traffic
including startup bytes, normal Modbus frames, writes, and inverter-originated
frames. It also showed that serial reads are delivered as chunks, while Modbus
frame boundaries are determined by protocol length/CRC rather than by the
serial read calls.

The evidence does not show that the broker intentionally merged two valid
Modbus frames into one protocol frame. A single serial read can contain bytes
from more than one frame, and a single response can arrive in several reads;
the framed broker must therefore extract and associate complete CRC-valid
frames rather than forwarding read-call boundaries as if they were frames.

Earlier captures also contained valid asynchronous inverter-originated traffic
with unit `0` and function `0`. Those frames were recorded as asynchronous and
forwarded to the Shine; they were not incorrectly consumed as the response to
an FC20 request. This is the relevant observation path for the undocumented
traffic suspected around Shine operation.

### FC20

The known normal request is:

```text
01 20 00 00 00 64 81 e6
```

It is an opaque function-20 transaction for unit 1, with a 100-word payload in
the observed protocol. The broker has intentionally not promoted FC20 words
to normal register semantics or assigned them to the DDSU666 meter.

The existing FC20 analysis established:

- repeated valid FC20 request/response pairs in live Shine traffic;
- successful responses of the expected large shape in healthy windows;
- occasional missing or late responses in unhealthy windows;
- response latency in the millisecond range when the inverter responds;
- no sufficient evidence yet to decode the complete payload or identify it as
  a meter protocol;
- no proof that a late FC20 response is caused by a specific fixed delay after
  one particular preceding block.

The current live log was still showing successful FC20 refreshes and virtual
Shine responses at inspection time. The long off-cloud capture and the portal
backlog behavior support the hypothesis that the datalogger/inverter path can
retain telemetry and upload it later, but they do not yet prove that the FC20
payload itself is the historical backlog.

The existing analysis scripts remain the authority for retained captures. No
new FC20 parser was added in this checkpoint.

### Portal/cloud behavior and settings

The portal tests showed that datalogger connectivity, inverter status, live
telemetry, totals, and settings pages are not one single health signal. The
following combinations were observed:

- the datalogger could be connected to the cloud while the inverter was shown
  offline;
- the portal could retain or display plausible cumulative totals while live
  telemetry was sparse or absent;
- battery and TOU pages sometimes displayed corrupt-looking values after an
  unhealthy Shine/inverter session and later returned to plausible values;
- Home Assistant remained stable through several of these Shine/cloud
  transitions.

One portal TOU change was observed as a direct Modbus write and read-back:

```text
FC16, holding registers H3040-H3041, data 0x2000 0x0700
```

The broker did not originate that write. This confirms that portal writes can
reach the inverter through the Shine path and that write traffic must remain
observable and correctly associated.

The winter-mode change was accepted by the portal with:

```text
period:             2026-09-12 .. 2026-09-19
enable:             enabled
on-grid stop SOC:   20%
off-grid stop SOC:  10%
```

No corresponding FC20 protocol change was established. The expected seasonal
boundary write was not yet observed; a bounded passive capture was left
running for that purpose. The vendor map identifies likely active cutoff
registers such as H3037 and H3082, but the winter feature's exact portal write
sequence remains an evidence task, not a completed mapping.

## Current write status

The current cache gateway is deliberately not a general TCP write gateway.
Standard TCP requests are handled through the cache service, which accepts
FC03/FC04 reads. FC06/FC10 are not cache reads and are currently rejected on
that path rather than blindly sent to the inverter.

The physical Shine passthrough path is different: validated Shine-originated
writes can be forwarded to the inverter, with exact response handling,
cache invalidation, and audit events in the current implementation. This is
why “the Shine may write” and “TCP clients may write” are separate contracts.

The eventual TCP write implementation needs, at minimum:

- separate `PROD_TCP` and `DEV_TCP` source identity in every request and audit
  event;
- an explicit reviewed register/function allowlist;
- an admin/armed or equivalent authorization boundary;
- one serialized physical write owner shared with Shine and polling;
- exact FC06/FC10 acknowledgement matching;
- no blind retry after an ambiguous timeout;
- invalidation of overlapping cache blocks;
- read-back validation where the register semantics require it;
- preservation of Shine timing and asynchronous response forwarding; and
- a safe rollback/disable switch.

This is a required next broker task before the development HA integration can
exercise writes through TCP. It is not implemented or enabled by this
checkpoint.

## What is proven, and what is not

| Area | Current conclusion |
|---|---|
| one physical inverter owner | proven in the cache architecture and live operation |
| HA access through `:5020`/`:5021` | proven for normal FC03/FC04 reads |
| cache slicing/coalescing | proven by focused tests and Stage-A live checks |
| native MIN/TL-XH block polling | implemented from the accepted family plan; continued hardware evidence remains useful |
| Shine hot-plug recovery | proven for the stable by-id open/reopen path |
| raw bidirectional Shine forwarding | proven in the bounded raw-transparent experiment |
| asynchronous inverter frames to Shine | observed and logged in framed combined captures |
| normal Shine session after every reset | not proven; several reset states stalled at discovery |
| FC20 complete semantics | not proven; it remains opaque |
| FC20 as DDSU666 data | not proven |
| portal winter boundary write | not yet observed |
| general TCP FC06/FC10 writes | not implemented in cache mode |
| no-Shine current deployment | false; current deployment includes `--shine` |
| supported no-Shine cache configuration | true: `--mode cache` with inverter only |

## Evidence and related reports

The detailed evidence remains in the existing reports and data files:

- [HA-DEV-2C Shine transport observation](HA-DEV-2C_SHINE_TRANSPORT.md)
- [HA-DEV-2D FC20 reverse engineering](HA-DEV-2D_FC20_REVERSE_ENGINEERING.md)
- [HA-DEV-2E discovery flush A/B test](HA-DEV-2E_SHINE_DISCOVERY_FLUSH_AB.md)
- [HA-DEV-3A cache gateway architecture](HA-DEV-3A_CACHE_GATEWAY_ARCHITECTURE.md)
- [HA-DEV-3C live cache gateway integration](HA-DEV-3C_CACHE_GATEWAY_LIVE_INTEGRATION.md)
- [HA-DEV-3C serial recovery](HA-DEV-3C_SERIAL_RECOVERY.md)
- [FC20 word analysis data](data/fc20_word_analysis.json)

The primary live evidence source remains the bounded JSONL sniff stream on
TCP 5700. Large persistent broker logs are supporting evidence only. The
existing analysers should continue to be used for all future bounded captures.

## Next phase: finish the HA integration

The next work should return to the primary product goal rather than expanding
the broker for its own sake:

1. complete and review the semantic, family-specific MIN/TL-XH register map;
2. map each HA entity to a physical source while preserving entity identity,
   units, signs, counter semantics, and Recorder statistics;
3. use the vendor-native block plan so the development container can exercise
   live reads without one Modbus transaction per entity;
4. compare cached block values against direct live evidence and existing HA
   entities before any source migration;
5. keep the no-Shine `--mode cache` configuration as the recovery and
   development fallback;
6. later add the reviewed TCP write contract; and
7. only then decide whether Shine passthrough remains a separate broker
   service or becomes an HA-integrated transport component.

The broker's purpose in this phase is to provide one observable, serialized,
recoverable path to the live inverter. It is not a reason to weaken the
semantic and statistical compatibility contract of the Home Assistant
integration.
