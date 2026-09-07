# HA-DEV-2C Shine transport observation

Date: 2026-09-07  
Broker branch: `fix/ha-dev-2b-forensics-20260906`  
Candidate: `121295f` (`--shine-policy transparent`)
Result: **GREEN WITH FOLLOW-UP** for the post-replug transport test; the
earlier pre-replug attempt documented below remains **NOT GREEN**.

## Device and wiring

The live host has two CH340-class USB serial adapters. Their Linux numbering
changed after the power-cycle, so tty numbers are not used as identity:

| role | stable path | observed tty after power-cycle |
|---|---|---|
| inverter | `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0` | `ttyUSB0` |
| ShineWiFi-X | `/dev/serial/by-id/usb-04e2_1410-if00-port0` | `ttyUSB1` |

Both candidate serial legs were configured as `115200 8N1`, based on the
fixed-speed Shine USB converter and the established inverter configuration.
The candidate used explicit Docker device mappings for both by-id paths.

## Bounded transparent capture

Primary capture: `/tmp/ha-dev-2c-sniff-20260907-164155-final.jsonl`  
Size: 15,244 bytes, 83 JSONL events  
SHA-256: `8e9740fe88db02a83f993dd4d81ffc3dae1516a1bac51da5b90ee784481fd024`

The existing `tools/analyze_sniff_log.py` reported for Shine:

```text
REQ=28 RSP=9 timeouts=18 drops=0 crc_bad=9 unknown_func=0
```

The 28 requests include physical retries and broker event pairs; the actual
Shine request shape observed throughout the capture was:

```text
0003002b0001f5d3
unit=0, function=0x03, address=43, count=1, CRC valid
```

No FC04, FC06, FC10, FC20, or other Shine function was observed in this
window. No Shine-originated write frame was observed or forwarded.

The Shine request repeated at approximately two seconds while the broker was
servicing its retry path. A complete request attempt took about five seconds
when no matching inverter response was available. The Shine then reported
itself offline. This is observed broker/transport timing, not a claimed Shine
firmware timeout value.

## Response association and production HA

The transparent candidate forwarded the Shine frames to the inverter, but no
response matching `unit=0/function=0x03` was delivered to Shine. Physical RX
also contained valid unit-1 responses associated with production HA traffic;
the exact standard matcher correctly did not assign those to the Shine
request.

Production HA continued to receive valid unit-1 responses and its own retry
behavior tolerated the short test. It did experience additional queue waits of
roughly 1–5 seconds while Shine requests occupied the single physical bus.
After rollback to the known-good `dfb99a8` broker without Shine passthrough, a
direct production-port FC04 read again succeeded.

The transparent candidate was stopped and the known-good broker was restored.
The candidate image/container were retained for follow-up; no production HA
upgrade or restart was performed.

## Transport changes and test status

The broker candidate adds:

- source-aware downstream scheduling: `SHINE` priority 0, production TCP
  priority 1, DEV TCP priority 2;
- bounded Shine burst arbitration so production is not permanently starved;
- structured Shine request/response/raw-link events;
- explicit `read-only` quarantine policy, with `transparent` as an explicit
  opt-in for controlled observation;
- exact FC06 response matching on unit/address/value/CRC;
- exact FC10 response matching on unit/start/quantity/CRC;
- standard matching for FC06/FC10 even when requests originate at Shine.

The relevant non-simulator suite passed: `47 passed`. Ruff E/F checks and
`git diff --check` passed. The existing full simulator incompatibilities remain
unchanged.

## Follow-up from the first attempt

The current evidence does not establish the correct Shine-to-inverter
association for `unit=0` traffic. Do not reinterpret a valid unit-1 response
as a Shine response merely because it is nearby on the shared serial bus.

That first attempt required a normal direct Shine session and a physical
unplug/replug repeat. Both are covered by the post-replug observation below.
The unit-0-to-unit-1 association is recorded as observed protocol behavior,
not as a conventional unit-matched Modbus response.

## Safety counters

```text
agent-originated Growatt writes = 0
Shine-originated write frames observed/forwarded = 1 (FC06 address 188, value 0)
DDSU666 writes = 0
Peblar commands = 0
Zoe commands = 0
production HA upgrade/restart = 0
```

### Historical NOT GREEN result

The pre-replug transparent attempt was not green: Shine went offline and the
unit-0 response association was unresolved. That state is superseded by the
post-power-cycle, unplug/replug observation below; the live broker is left on
the combined HA-plus-Shine candidate.

## Post-replug transparent observation

After the inverter power-cycle, the Shine was physically unplugged and then
plugged back in while the broker was running with `/dev` mounted. The broker
reported repeated `shine_open_failed` events while the by-id path was absent,
then reported `shine_online` when the path reappeared. This demonstrates the
required hotplug handling without relying on tty numbering.

The first post-replug probe was:

```text
0003002b0001f5d3
unit=0, FC03, address=43, count=1, CRC valid
```

The inverter response observed on the raw serial path was:

```text
01030213ecb4f9
unit=1, FC03, one register, CRC valid
```

Transparent mode passed that raw response to the Shine. The differing unit
bytes are a live protocol observation; this report does not reinterpret it as
a conventional unit-matched response.

The Shine subsequently produced normal traffic including FC03 reads at
addresses 0 (125 words), 180 (20 words), and 209 (15 words), FC04 reads at
3000 and 3125 (125 words), and a live FC20 request/response. An FC06 request
at address 188 with value 0 was also observed and forwarded; it was
Shine-originated, not generated by the agent.

The later combined HA-plus-Shine bounded capture was:

```text
/tmp/ha-dev-2c-shine-full-20260907-171536.jsonl
2026-09-07T17:15:36.859Z .. 2026-09-07T17:17:46.340Z
142232 bytes, SHA-256:
47a1ac621574cffa86100ff8a7f9e9b143bd9dced24c0ffe4cfee2cbab968f7d
```

`tools/analyze_sniff_log.py --include-tcp` reported:

```text
SHINE       REQ=138 RSP=135 timeouts=6 drops=0 crc_bad=3 unknown_func=0
PROD_TCP    REQ=5   RSP=5   timeouts=0 drops=0 crc_bad=0 unknown_func=0
```

The analyzer's six Shine timeout records represent three timed-out
transactions; they occurred while production HA was using the shared bus.
The Shine continued polling and did not report `shine_offline` in this
window. Production HA received all five captured responses, including its
large FC04 reads, though queue waits reached about 3 seconds. The broker
remained running with restart count zero.

This establishes functional transparent transport and hotplug recovery, but
not yet a final Shine portal/cloud-online result or a semantic interpretation
of FC20. Those remain follow-up validation items. The capture is primary
runtime evidence; the existing analyzer was used without a new parser.
