# HA-DEV-2 — PyModbus transaction-ID and asynchronous transport diagnosis

Date: 2026-09-06 (UTC)

Status: transport diagnosis complete; write readiness is **NOT GREEN**.

This report continues the published HA-DEV-1R rehearsal on HA-core branch
`growatt-local-test` at commit `6fe3e6758c1c46808a7e22be6b9be785d4a27eba`.
That commit is unchanged and its remote branch was verified equal before this
follow-up.  This report is on the separate branch
`research/ha-dev-2-transport-20260906`.

## Scope and safety

- HA Core development version: 2026.9.0.
- PyModbus: 3.13.1.
- Candidate Growatt integration: 0.2.1, commit `b72ddef4c5e5a85e156c54baf2cf96bee2241515`.
- TCP endpoint: broker `192.168.1.148:5021`, Modbus unit 1.
- The live broker's physical serial side is 115200 8N1.
- Reads used the existing broker only. No inverter, meter, battery, Peblar,
  production Home Assistant, or configuration writes were performed.
- Production HA was not upgraded or modified.

The dev client and the existing production client shared the broker during the
bounded observation. They were distinguishable by TCP source. The dev client
in the primary sniff window was `TCP:192.168.1.139:39466`; the concurrent
production client was `TCP:192.168.1.148:34844`.

## Transport model required by the hardware

The inverter-side RS485 stream must be treated as asynchronous. A physical
response is not safe to associate solely because it is the next bytes received:
the inverter can emit unsolicited or delayed ShineWiFi/internal traffic, and
the current broker drains and clears raw serial input before a new request.
The association contract therefore has to validate unit, function, response
shape/byte count, requested quantity, timing, and CRC, and fail closed when
association is ambiguous.

The physical scheduler must remain global and serialized:

- one outstanding physical request;
- at least 850 ms between physical commands, with approximately 1 second as
  the normal cadence;
- many TCP clients may queue behind that scheduler;
- a TCP retry must not become a second physical transaction when it is an
  exact duplicate of a request already in flight or recently completed.

These observations are compatible with the vendor-declared V1.24 minimum
command period and do not establish that every quiet interval is specifically
reserved for ShineWiFi frames.

## Bounded HIL evidence

The primary capture was a fresh bounded broker sniff capture on TCP port 5700,
started before the read-only HIL run:

| Artifact | Result |
|---|---|
| Capture | `/tmp/ha-dev-2-sniff-20260906-1840.jsonl` |
| Capture SHA-256 | `2e3c144a69987c369e8c172162816189fff7cd234d8963b2dbbe198c8ea0b234` |
| HIL results | `/tmp/ha-dev-2-hil-results-20260906-1840.jsonl` |
| HIL results SHA-256 | `47e512df6b18be80ebad691b7d9abfe024205058b72e16f0c40fa474a4bc2b0a` |
| HIL window | 18:41:17.493–18:42:11.596 UTC |
| Logical operations | 28: 20 FAST and 8 FULL |
| Logical result | 28/28 successful, 125 words each |
| Physical dev requests/responses | 37/35 |
| Broker downstream retries | 3 |
| Minimum dev physical request spacing | 1.083 s |
| Broker analyzer result | `timeouts=0 drops=0 crc_bad=0 unk_func=0` for the dev source |

The existing broker analyzer was used:

```text
python3 tools/analyze_sniff_log.py /tmp/ha-dev-2-sniff-20260906-1840.jsonl \
  --include-tcp --since 2026-09-06T18:41:17Z --until 2026-09-06T18:42:12Z
```

The 28/28 logical result is not sufficient to declare the transport safe. The
37/35 physical count and retries show that the TCP/client interaction still
needs explicit transaction handling. The sniff analyzer reports broker logical
events; it is not a raw physical-byte capture.

## PyModbus mismatch evidence

The latest bounded PyModbus trace contains 20 FAST reads:

- trace: `/tmp/ha-dev-2-pymodbus-trace-20260906-1845.jsonl`
- trace SHA-256: `49912584a6fe210ffc86cd8bfaf5efed0995eb78d4e526db35ea67f289530b69`
- stderr: `/tmp/ha-dev-2-pymodbus-3.13.1-stderr-20260906-1845.log`
- stderr SHA-256: `239779d7c77a15a283251de7fa8013a2ddd8d2be123ebbdbcd2d11655e102b50`

The trace has 21 transmitted requests, 21 received responses, one client
retry, and one transaction-ID mismatch. All 20 logical reads eventually
completed. The exact sequence is:

1. At `18:43:16.164`, TID 13 sent FC04 input start 3000, count 125.
2. At `18:43:17.254`, TID 13 received a 259-byte FC04 response.
3. At `18:43:17.256`, TID 14 sent FC04 input start 3125, count 125.
4. TID 14 timed out at the PyModbus 3-second boundary and was retried at
   `18:43:20.260` with the same TID and identical request.
5. At `18:43:20.849`, TID 14's response arrived.
6. At `18:43:20.850`, TID 15 was sent for FC04 input start 3000, count 125.
7. At `18:43:21.940`, a late 259-byte FC04 response with TID 14 arrived.
   PyModbus logged:

   ```text
   ERROR: request ask for transaction_id=15 but got id=14, Skipping.
   ```

8. At `18:43:23.033`, the correct TID 15 response arrived.

The response payload does not contain the requested start address; the
`start=64000`/`count` values shown by the generic trace formatter are decoded
payload words, not evidence of a different Modbus request. The request context
above comes from the transmitted MBAP/PDU.

## Independent raw-TCP proof

To exclude a PyModbus logging-only false positive, a temporary local TCP proxy
captured broker-to-client bytes independently:

- raw wire: `/tmp/ha-dev-2-tcp-wire-20260906.jsonl`
- raw wire SHA-256: `116eba979d514dc272cb396e4ca2d5a99cd62aeaa735a18b5064bfe3c12cdad2`
- matching PyModbus trace SHA-256:
  `338ece46c0ffa2695507dd4cb6ce889854d853193fb1a10a5e0409ae2a9dccd4`

The relevant wire sequence was:

| UTC | Direction | TID | Request |
|---|---|---:|---|
| 18:35:25.630 | client → broker | 4 | FC04/I3125/125 |
| 18:35:28.635 | client → broker | 4 | exact retry |
| 18:35:29.223 | broker → client | 4 | first response |
| 18:35:29.226 | client → broker | 5 | FC04/I3000/125 |
| 18:35:30.317 | broker → client | 4 | late duplicate response |
| 18:35:32.228 | client → broker | 5 | exact retry |
| 18:35:33.909 | broker → client | 5 | response |
| 18:35:33.911 | client → broker | 6 | FC04/I3125/125 |
| 18:35:35.002 | broker → client | 5 | late duplicate response |
| 18:35:36.097 | broker → client | 6 | response |

The live broker preserves the TID from each queued TCP request. The stale TID
therefore is not an MBAP rewrite: the client retry was queued and later caused
a second physical transaction whose response arrived after the client had
advanced to the next TID.

A separate raw Modbus-TCP client that did not retry sent ten sequential reads
with TIDs 1–10; all responses matched their TIDs. This isolates the trigger to
the retry/timeout interaction rather than an inherent TID corruption on every
request.

## Root-cause classification

Primary classification: **`STALE_RESPONSE_AFTER_TIMEOUT`**.

PyModbus's 3-second timeout/retry policy can resend a request while the broker
is still constrained by its downstream retry and approximately 1-second
physical pacing. The broker then processes the queued duplicate as another
physical transaction. Its response retains the duplicate's original TID, but
that response is stale when it reaches the client after the client has moved to
the next request.

Rejected alternatives:

- `BROKER_MBAP_ID_REWRITE_BUG`: raw wire responses preserve each queued
  request's TID; the issue is ordering of a duplicate, not rewriting.
- `LOGGING_ONLY_FALSE_POSITIVE`: the late response is present in independent
  broker-to-client bytes.
- `REAL_RESPONSE_MISASSOCIATION`: the TCP layer demonstrates stale duplicate
  ordering; physical RTU association remains a separate asynchronous-stream
  concern, not proof of TCP-level response substitution.
- A generic client transaction-manager incompatibility may describe a
  contributing condition, but it is not the primary observed failure mode.

## Broker-side mitigation

A narrowly scoped broker fix was implemented and pushed separately:

- branch: `fix/stale-tcp-response-20260906`
- commit: `f4c749890b09edac19727dfaa0be63179c828bb6`
- remote branch was verified equal to the local commit.

The fix adds a bounded per-connection cache keyed by the exact MBAP request
(`tid + pid + uid + pdu`). An exact duplicate within the short TTL replays the
cached complete response and does not issue a second downstream physical
transaction. It also emits a duplicate-replay event for observability.

Validation:

- targeted cache test: 1 passed;
- non-simulator broker tests: 34 passed;
- full broker suite: 34 passed, 9 pre-existing simulator failures caused by
  the current PyModbus 3.13.1/environment and the simulator's `address=0`
  handling;
- `ruff` on the new test's E/F checks: passed;
- `git diff --check`: passed.

The fix has **not** been deployed to the live RPi broker. The live HIL evidence
above therefore remains evidence against the old broker revision and is not a
post-fix acceptance result.

## Asynchronous physical-stream gap

The broker's current `Downstream.transact` drains the serial port's
`in_waiting` property and clears the framer buffer before sending a request. Consequently the current 5700
sniff stream cannot prove that unsolicited ShineWiFi/internal frames were
absent: such bytes may be discarded before classification. The analyzer's
zero CRC/drop/unknown counts must not be reported as “zero unsolicited
frames”.

The required follow-up is a broker-controlled, timestamped raw serial capture
that records every physical RX frame without opening a second Modbus master.
It must classify expected, unsolicited, late, malformed, and unknown frames
using unit/function/bytecount/quantity/timing/CRC, then repeat at least 20
read operations with the same global pacing. The RPi USB-RS485 observer must
remain receive-only if an external physical observation is used. No writes are
permitted during that validation.

Until that capture exists, and until the duplicate-replay fix is deployed and
retested against the live path, write readiness is blocked.

## Final disposition

**NOT GREEN**

The stale-response diagnosis is reproducible and independently proven, and a
reviewable broker mitigation exists. The HA-DEV-2 transport/write acceptance
is nevertheless not green because the mitigation is not deployed and the
asynchronous physical RX stream has not yet been captured and classified.
