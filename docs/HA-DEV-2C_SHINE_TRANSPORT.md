# HA-DEV-2C Shine transport observation

Date: 2026-09-07  
Broker branch: `fix/ha-dev-2b-forensics-20260906`  
Candidate: `29340e8` (`--shine-policy transparent`)  
Result: **NOT GREEN**

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

## Follow-up required

The current evidence does not establish the correct Shine-to-inverter
association for `unit=0` traffic. Do not reinterpret a valid unit-1 response
as a Shine response merely because it is nearby on the shared serial bus.

Before leaving transparent passthrough enabled, capture a normal direct Shine
session or otherwise establish the protocol rule that maps this request to its
inverter response. Then repeat the test with physical unplug/replug while
measuring Shine reconnect behavior. FC20 and any Shine writes remain
uncharacterized because they did not occur in this bounded window.

## Safety counters

```text
agent-originated Growatt writes = 0
Shine-originated write frames observed/forwarded = 0
DDSU666 writes = 0
Peblar commands = 0
Zoe commands = 0
production HA upgrade/restart = 0
```

### NOT GREEN

Transparent Shine passthrough is not yet stable alongside production HA and
the Shine: Shine went offline and response association for its unit-0 request
remains unresolved. The broker is left on the known-good no-Shine state.
