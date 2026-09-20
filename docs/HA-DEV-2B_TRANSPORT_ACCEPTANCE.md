# HA-DEV-2B — duplicate TCP suppression and asynchronous RX acceptance

Date: 2026-09-06 (UTC)

Final disposition: **NOT GREEN** for Growatt write readiness.

The corrected broker is running in production read-only mode. The TCP stale
retry failure was fixed and the bounded live read-only run was clean. Write
readiness remains blocked because the live run did not exercise an actual
client retry and the physical matcher still cannot prove register identity for
a hypothetical unsolicited frame with the same unit/function/length shape.

## Starting point and safety

This continues HA-DEV-2 at HA-core commit
`ef46a27ebc1405dc350030d6fbecf3c2b02d20a1` on
`research/ha-dev-2-transport-20260906`.

The HA-DEV-2 primary diagnosis was `STALE_RESPONSE_AFTER_TIMEOUT`: PyModbus
resent an exact request while the broker was still servicing the first request,
the broker processed the queued duplicate physically, and the duplicate
response arrived after the client had advanced to the next TID.

No Growatt, DDSU666, Peblar, or Zoe writes were performed. Home Assistant Core
was not restarted or upgraded. The inverter was not restarted or reconfigured.

## Duplicate retry

### Old behavior

For an exact same-connection request tuple
`TID + PID + unit + PDU`, commit `f4c749890b09ed19727dfaa0be63179c828bb6`
prevented the second physical transaction but replayed the cached response.
That could still produce a stale duplicate TCP response after PyModbus had
accepted the first response.

### Corrected behavior

Broker branch: `fix/ha-dev-2b-forensics-20260906`

- runtime commit deployed: `dfb99a8705ab88145e252f464d61ecd789e92f2a`;
- final branch HEAD, including the analyzer correction:
  `0cc374432995032c4999ccb1be5e8d7f59740c1e`;
- local and remote branch heads were verified equal.

An exact duplicate within the bounded connection-local cache window now emits
`tcp_duplicate_suppressed`, causes no second physical transaction, and sends
no second TCP response. Different TIDs, different PDUs, new connections, and
expired entries remain independent.

The suppression is safe for an already-served retry because the original
response has been sent over the same live TCP connection; TCP supplies ordered
reliable delivery. This rule is not generalized to independent requests.

### Regression evidence

The old deterministic sequence produced TCP response TIDs `4, 4, 5` and two
physical calls. The corrected test models the same queued retry and produces
TIDs `4, 5` with exactly two physical calls. Additional tests cover:

- same PDU with a different TID;
- same TID with a different PDU;
- cache expiry;
- cache isolation after reconnect;
- actual PyModbus 3.13.1 retry behavior.

Focused duplicate/framer/forensic tests: **36 passed**.

## Timeout budget

The live broker uses:

- physical minimum command period: 1.0 s;
- downstream timeout: 1.5 s;
- broker-owned standard-read attempts: at most two;
- global serialized physical scheduler shared by production and DEV.

One broker transaction can therefore consume approximately 3.0 s when the
first physical attempt times out and the broker performs its retry. With one
transaction already ahead, a bounded service time is approximately 6.0 s,
plus scheduling jitter. The development HIL client used `timeout=8 s` and
`retries=0`, allowing the broker to own physical retries without a competing
client retry loop. The maximum observed logical operation was 3595.5 ms.

This timeout choice was used only by the development HIL client. Production
Home Assistant timeout settings were not changed. Duplicate suppression
remains defense in depth.

## Production broker deployment

### Old deployment recorded before the swap

- container: `growatt-broker-ha2c-b42445`;
- image/tag: `growatt-rtu-broker:b42445f`;
- image ID: `sha256:139d827f1c3a2a59743802fb18497b42151cc649e7a1820fd743cf8f84f423bf`;
- serial mapping: host
  `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0` to container
  `/dev/inverter`;
- ports: host 5020, 5021, and 5700 to the same container ports;
- serial: 115200 8N1;
- pacing: `--min-period 1.0`;
- downstream timeout: `--rtimeout 1.5`;
- network: Docker bridge with published ports;
- restart policy: `unless-stopped`;
- container command did not use a Shine serial endpoint.

The old image was preserved as
`growatt-rtu-broker:pre-ha-dev-2b-b42445f`. The original container was kept
stopped, so rollback is deterministic:

```text
docker stop growatt-broker-ha2c-dfb99a8
docker start growatt-broker-ha2c-b42445
```

No old image was deleted or pruned.

### Candidate and outage

The candidate was built on the RPi arm64 host before the outage:

- source runtime commit: `dfb99a8705ab88145e252f464d61ecd789e92f2a`;
- image/tag: `growatt-rtu-broker:ha-dev-2b-dfb99a8`;
- image ID: `sha256:b7b064f575df09083bf571d24810eadee890f20f43a1672a573ff708ff4ab2e7`;
- architecture: arm64;
- final running container: `growatt-broker-ha2c-dfb99a8`;
- final container ID: `6a93eb06a96f33f9301e5ada790a419d91b15e10d5240423f394c9609f70132f`;
- final state: running, restart count 0;
- final command retains ports 5020/5021/5700, `/dev/inverter`, 115200 8N1,
  1.0 s pacing, and 1.5 s downstream timeout.

The initial container swap began at approximately 21:11:12 CEST. The old
container stopped at 21:11:24 and the candidate was healthy at 21:11:26:
approximately 14 seconds of swap outage, below the five-minute target.

After forensic capture, the candidate was recreated without forensic mode from
21:25:37 to 21:25:46 CEST, approximately 9 seconds. Production HA then
continued successful `growatt_local` fetches; a representative live log at
21:26:41.773 reports `success: True`. HA Core itself was not restarted.

The forensic option is not active in the final running command.

## Bounded live HIL acceptance

The final read-only window used the DEV endpoint `192.168.1.148:5021`, unit 1,
with production HA polling concurrently.

| Item | Evidence |
|---|---|
| HIL window | 19:21:04.545–19:22:17.415 UTC |
| Logical reads | 56: 40 FAST and 16 FULL |
| Logical result | 56/56 successful, 125 words each |
| HIL client timeout/retries | 8 s / 0 |
| PyModbus trace | 56 TX, 56 RX |
| TID mismatches | 0 |
| Orphan PDUs | 0 |
| Client retries | 0 |
| Maximum logical duration | 3595.5 ms |
| Broker DEV physical requests | 59 |
| Broker DEV responses | 56 |
| Broker physical retries | 3 |
| `tcp_duplicate_suppressed` | 0 in this clean no-client-retry window |
| `tcp_duplicate_replay` | 0 |

Primary bounded broker sniff capture, collected from TCP port 5700 before the
HIL window:

- path: `/tmp/ha-dev-2b-sniff-20260906-192100.jsonl`;
- 132 JSONL events, 57775 bytes;
- SHA-256:
  `5dc6f9bae379273fb3667ead2e38381dd4d6abd0760a061396710a0891dd2a31`;
- existing analyzer result for DEV source
  `TCP:192.168.1.139:48126`: `REQ=59 RSP=56 timeouts=0 drops=0
  crc_bad=0 unk_func=0`;
- concurrent production sources remained separate and also had no timeout/drop/
  CRC/unknown events.

HIL artifacts, retained outside Git:

- results: `/tmp/ha-dev-2b-hil-results-20260906-192100.jsonl`, SHA-256
  `ddb5dc31af0f06678c67dd1462d52f8520023983cb7c493ecde56f325c7692a7`;
- PyModbus trace: `/tmp/ha-dev-2b-pymodbus-trace-20260906-192100.jsonl`,
  SHA-256
  `64c7ae953478c05c9ed5cda711e0c6b0c829a8eb0ec55af6b9c558027078a6d3`.

## Physical RX forensics

The candidate was run with an explicit bounded forensic option. No second
serial reader or Modbus master was used. The broker remained the sole owner of
the inverter serial device. The capture recorded physical TX events and every
serial RX read, including fragmented 32-byte reads, pre-request drains,
leftover/residual buffers, and the explicit final drain.

Full retained capture:

- path: `/tmp/ha-dev-2b-forensic-final-local.jsonl`;
- 2760 JSONL records, 778675 bytes;
- SHA-256:
  `8a9c46cb28c74d66bcd21029f64bc49ca01c60e792e08b9700fcf3b0f464cefa`;
- physical TX records: 349;
- raw serial RX bytes: 75697;
- candidate frames: 331;
- unknown bytes: 0;
- frame categories: 331 `EXPECTED_STANDARD_RESPONSE`, 0 late, 0 unsolicited
  valid-CRC, 0 malformed/partial candidates.

The same broad time window as the final sniff/HIL run was analyzed separately:

- derived window: 19:21:03.000–19:22:20.000 UTC;
- 533 records, 150660 bytes;
- SHA-256:
  `0ce8ce540f2f49bd8bec5b788c3af1c333c5fe0f87a22f424967336157c146b6`;
- 66 physical TX records, 14723 raw RX bytes;
- 63 candidate frames, all expected standard responses;
- unknown bytes: 0.

### Function-code inventory

| Function | Hex | Total | Expected | Unsolicited | CRC-valid | Lengths |
|---:|---:|---:|---:|---:|---:|---|
| 3 | `0x03` | 12 | 12 | 0 | 12 | 7, 255 |
| 4 | `0x04` | 51 | 51 | 0 | 51 | 69, 141, 205, 255 |
| 6 | `0x06` | 0 | 0 | 0 | 0 | — |
| 16 | `0x10` | 0 | 0 | 0 | 0 | — |
| 32 | `0x20` | 0 | 0 | 0 | 0 | — |

The table is for the broad HIL window. The full capture likewise observed only
FC03 and FC04 (60 and 271 candidates respectively). `FC0x20 observed: NO`.

The raw classifier reconstructs contiguous serial-read chunks before parsing;
it does not treat an arbitrary CRC-valid substring as a frame. This matters
because the inverter responses arrive in multiple 32-byte reads. The existing
broker analyzer and the new forensic classifier were used; no ad-hoc live
parser was used.

### Timing and pacing

Across physical TX events in the full forensic capture:

- minimum spacing: 1.016 s;
- median spacing: 1.092 s.

In the broad HIL window:

- 66 physical TX events;
- minimum spacing: 1.017 s;
- median spacing: 1.093 s;
- maximum observed interval: 2.502 s;
- three broker-owned downstream retries.

This confirms global command pacing remained above the vendor minimum of
850 ms and close to the 1-second target across production and DEV clients.
The capture did not establish causation between the quiet interval and any
Shine/internal traffic; no such traffic was observed in this bounded window.

## Protocol findings

### Proven in this run

- FC03 and FC04 standard responses were CRC-valid and structurally matched to
  outstanding requests.
- No FC06, FC10, or FC20 was observed.
- No valid unsolicited or late frame was observed in the bounded raw stream.
- No evidence required an XOR/encryption/obfuscation transform.
- The local reverse-engineering material was inspected but not promoted to live
  protocol evidence. Existing provenance includes `rev_eng.txt`,
  `rev_eng_full.txt`, `rev_eng_words.txt`,
  `tools/reverse_engineer_growatt.py`, `tools/build_sample_sets.py`, and
  `docs/data/shine_sample_sets.json`. Those artifacts contain Shine-side
  function-`0x20` examples and remain third-party/local research.

### Still hypotheses or unresolved

- The external Growatt-family FC0x20 meter-proxy hypothesis remains unproven for
  this MIN 6000TL-XH. `FC0x20` was not observed.
- No candidate frame was available for correlation with P1, Growatt load/grid,
  PV, or battery telemetry.
- A same-shape unsolicited FC03/FC04 frame cannot be distinguished by register
  address in its response payload alone. The bounded run observed none, but
  that is not proof that such a frame can never occur.

## Validation summary

- focused duplicate/framer/forensic tests: 36 passed;
- broker non-simulator suite: 42 passed;
- full broker suite: 42 passed, 9 failures;
- all 9 failures are the known simulator incompatibility with the current
  PyModbus 3.13.1 environment: simulator construction passes `address=0`,
  which the installed simulator converts to `address=-1`;
- Ruff E/F checks for new tests and forensic tooling: passed;
- `git diff --check`: passed.

The simulator failures were not changed as part of this transport task.

## Production impact and safety counters

- Growatt writes: **0**;
- DDSU666/meter writes: **0**;
- Peblar commands: **0**;
- Zoe commands: **0**;
- production HA Core upgrade/restart: **0**;
- inverter restart/reconfiguration: **0**;
- final broker: candidate image `growatt-rtu-broker:ha-dev-2b-dfb99a8`,
  running normally on ports 5020/5021/5700;
- rollback image and old container remain preserved.

## Final disposition

**NOT GREEN**

The corrected broker eliminates the demonstrated stale duplicate response, the
actual PyModbus 3.13.1 retry path is green locally, the live read-only HIL has
0 TID mismatches and 0 orphan PDUs, physical pacing is compliant, and the
bounded physical stream contained no unclassified or unsolicited frames.

Write readiness is still not green because the live clean run had no client
retry to observe `tcp_duplicate_suppressed`, and the response matcher cannot
prove register identity for a hypothetical same-shape unsolicited FC03/FC04
frame. A future write gate should add an explicitly forced bounded live retry
test and, if required, strengthen that physical association contract before
any Growatt write is considered.
