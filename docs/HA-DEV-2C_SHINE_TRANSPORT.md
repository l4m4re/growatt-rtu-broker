# HA-DEV-2C Shine transport observation

Date: 2026-09-07  
Broker branch: `fix/ha-dev-2b-forensics-20260906`  
Historical candidate: `121295f` (`--shine-policy transparent`)
Latest candidate: `--shine-policy raw-transparent`, image
`growatt-rtu-broker:ha-dev-2c-raw-20260907`

Result: **RAW FORWARDING AND HOTPLUG GREEN; SHINE RUNTIME NOT GREEN**.

The raw bridge forwarded both directions byte-for-byte, including Shine
bootloader/debug traffic, and recovered the Shine USB path after physical
unplug/replug. It did not restore a stable Shine-to-inverter session: after a
Shine reset/reconnect the Shine repeatedly sent `0003002b0001f5d3` without an
inverter response. After the Shine experiment, the known-good no-Shine broker
was restored and dev-HA again received valid inverter responses.

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

The transparent candidate was stopped and the known-good no-Shine broker was
restored. Dev-HA was then started again against that broker and received valid
responses. No production HA upgrade or restart was performed.

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
unit-0 response association was unresolved. The later raw-wire experiment
resolved the transport question (all bytes were forwarded) but did not resolve
the Shine runtime state. The final live state is the known-good no-Shine
broker with dev-HA restored.

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

This historical framed-mode run established a useful baseline, but its
response association must not be used as proof that unit 0 is a conventional
Modbus slave response. The later raw-wire run below is the authoritative
transport result.

## Latest raw-wire HA+Shine experiment

### Objective and deployment

The framed `transparent` mode was replaced for this experiment by a separate
`raw-transparent` mode. It has no Modbus parser, scheduler, retry, timeout, or
minimum-period logic in the Shine path. It opens both serial devices at
115200 8N1, forwards every byte/chunk immediately in both directions, and
records the forwarded wire events for observation. No TCP listener was exposed
to HA during this phase; only the passive sniff stream on TCP 5700 was
published.

The live paths were:

```text
inverter  /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
ShineWiFi /dev/serial/by-id/usb-04e2_1410-if00-port0
```

The raw container was `growatt-broker-ha-dev2c-raw`. It was stopped after the
experiment; the known-good no-Shine container was then started with HA TCP
ports 5020 and 5021.

### Normal raw-wire capture

Primary sniff capture:

```text
/tmp/growatt-ha-dev2c-raw-20260907-184343.jsonl
2026-09-07T18:43:44.353 .. 2026-09-07T18:44:47.934 UTC
86,233 bytes, 367 WIRE events
SHA-256: 56fa2ed1fcf859b8fb7e75793f71afbce9084b5bea6855161d85117c63253560
```

Observed in this bounded window:

```text
Shine -> inverter: 48 chunks / 384 bytes
inverter -> Shine: 319 chunks / 9,910 bytes
all 48 Shine requests: CRC-valid 8-byte frames
complete CRC-valid response groups: 46
no-response groups: 2 (FC04 start 3125, count 125)
```

The successful response shapes were 205-byte FC20 responses, 45-byte FC03
responses, and 255-byte FC03/FC04 125-word responses. The two missing input
3125 responses were followed by Shine-generated retries; the raw bridge did
not generate those retries. Request intervals included approximately 1.5 s
normal spacing and approximately 0.5 s transitions, demonstrating that this
mode imposed no artificial one-second pacing.

The existing `tools/analyze_sniff_log.py` was run against this capture. It
reported no REQ/RSP records because raw mode deliberately emits `WIRE` events,
not reconstructed protocol events. The existing
`tools/analyze_forensic_rx.py` was also run against the corresponding
`shine_wire_raw` forensic capture; that tool correctly reported no
`physical_rx_raw` records because this mode does not manufacture physical
request/response associations. Supplemental counts above were calculated from
the captured JSONL directly. No new parser script was created.

### Hotplug and reset capture

Primary hotplug capture:

```text
/tmp/growatt-ha-dev2c-raw-hotplug-20260907-184646.jsonl
2026-09-07T18:46:47.355 .. 2026-09-07T19:01:45.989 UTC
350,508 bytes, 1,540 events
SHA-256: 102061eed1e43e086a3c75f8912343cba3d6794e63798a844202df0edbc9ed2b
```

It contained 1,523 WIRE events, 464 Shine-to-inverter chunks, and 1,059
inverter-to-Shine chunks. There were three Shine serial I/O errors/offline
events and three subsequent `shine_online` events. While the USB path was
absent, the broker retried opening the stable by-id path; when the path
returned, the bridge reopened it without a broker restart or tty-number
assumption.

The remote Shine reset produced printable boot/debug traffic such as
`rst cause`, `boot mode`, `SPI Flash`, and `jump to run user1`, followed by
binary boot data. Every chunk was labelled `shine_to_inverter` and
`disposition=forwarded`. This proves that the raw bridge did not block or
reinterpret Shine startup traffic.

After the reset/reconnect, the Shine repeatedly sent:

```text
0003002b0001f5d3
unit=0, FC03, address=43, count=1, CRC valid
```

No capture contains an inverter response to this exact frame. Earlier in the
same experiment the inverter did answer other Shine frames, including unit-1
FC03/FC04/FC20 traffic. Therefore the evidence does not support claiming that
the inverter ever answered this unit-0 frame. Unit 0 is also not a normal
positive proof of inverter health; the current post-reset Shine state never
sent the normal unit-1 telemetry requests.

After WiFi reconfiguration, the portal reported the datalogger as connected
with signal `Good (-51)`, device `ShineWiFi-X`, and firmware `3.1.0.5`. That
proves the datalogger's cloud/WiFi connection, not a working local inverter
Modbus session. The local wire capture still contained only the repeated
unit-0 frame:

```text
/tmp/growatt-ha-dev2c-raw-wifi-rejoin-20260907-194005.jsonl
2026-09-07T19:40:06.370 .. 2026-09-07T19:42:04.371 UTC
11,640 bytes, 60 WIRE events, all Shine -> inverter
SHA-256: 0ee1593f1de98d574979d9476918f5fa439b0e0489f93df02dd727d87d1aa718
```

An inverter power-cycle followed by another Shine unplug/replug did not
change that state:

```text
/tmp/growatt-ha-dev2c-raw-after-powercycle-20260907-195647.jsonl
2026-09-07T19:56:48.661 .. 2026-09-07T20:01:46.664 UTC
29,100 bytes, 150 WIRE events, all Shine -> inverter
SHA-256: 7ca035c170cb324a0e590b6f47e2bc7b7237fbeaf2d2a7659daaf2b819a2c698
```

The bridge generated no inverter writes, retries, or substitute responses in
any of these raw-wire phases.

### Final rollback and HA verification

The raw container was stopped and the known-good no-Shine broker
`growatt-broker-ha2c-dfb99a8` was started with:

```text
TCP 0.0.0.0:5020 and 0.0.0.0:5021
inverter /dev/inverter @ 115200 8N1
minimum command period 1.0 s
sniff 0.0.0.0:5700
```

Dev-HA was then started with the existing development configuration. At
`2026-09-07T20:14:21 UTC`, HA client `192.168.1.139` issued normal unit-1
requests and received CRC-valid responses, including:

```text
FC03 start 3000 count 125
FC04 start 3000 count 125
FC04 start 3125 count 125
```

The broker remained running with restart count zero. The accepted operational
state at the end of the experiment is therefore **no Shine passthrough,
dev-HA active, known-good broker**.

### Final conclusions and follow-up

The raw bridge is suitable as a bounded diagnostic transport experiment: it
preserves asynchronous Shine bytes, forwards boot/debug traffic, and handles
Shine USB hotplug. It is not accepted as the production combined HA+Shine
transport because the Shine session did not return to stable normal telemetry
after reset/reconnect, and the meaning of the unit-0 frame remains unresolved.

The next Shine investigation should use the raw bridge only with explicit
bounded capture and should not inject a substitute Modbus master. Production
operation remains the known-good no-Shine broker with HA. Any future migration
back to combined operation requires a fresh validation of the Shine startup
state, normal unit-1 telemetry, portal data continuity, and HA response
quality.
