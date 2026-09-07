# HA-DEV-2D FC20 reverse engineering and Shine discovery

Date: 2026-09-07  
Branch: `fix/ha-dev-2b-forensics-20260906`  
Device: live `MIN 6000TL-XH`, unit 1  
Result: **FC20 structure decoded; semantic and meter attribution remains evidence-gated**

This is an offline analysis of the raw-transparent HA+Shine experiment. The
live system was not reconnected or reset for this task. The final live state
remains the known-good no-Shine broker with development HA available. No
Growatt, DDSU666, HA, Peblar, or Zoe writes were performed.

The machine-readable result is
[fc20_word_analysis.json](data/fc20_word_analysis.json). It contains every
recovered request/response, all 100 FC20 words per response, unsigned and
signed interpretations, all adjacent big-endian 32-bit candidates, and the
contemporaneous standard FC03/FC04 snapshots. The raw JSONL files remain
outside git.

## Evidence inventory

The primary healthy capture is a fresh bounded sniff-stream capture from the
broker's TCP `5700` event stream. The other captures are supporting live-device
observations from HA-DEV-2C.

| Capture | Bytes | SHA-256 | Use |
| --- | ---: | --- | --- |
| `/tmp/growatt-ha-dev2c-raw-20260907-184343.jsonl` | 86,233 | `56fa2ed1fcf859b8fb7e75793f71afbce9084b5bea6855161d85117c63253560` | healthy bounded FC20/standard traffic |
| `/tmp/growatt-ha-dev2c-raw-hotplug-20260907-184646.jsonl` | 350,508 | `102061eed1e43e086a3c75f8912343cba3d6794e63798a844202df0edbc9ed2b` | unplug/replug, resets, raw forwarding |
| `/tmp/growatt-ha-dev2c-raw-wifi-rejoin-20260907-194005.jsonl` | 11,640 | `0ee1593f1de98d574979d9476918f5fa439b0e0489f93df02dd727d87d1aa718` | repeated unit-0 discovery request |
| `/tmp/growatt-ha-dev2c-raw-after-powercycle-20260907-195647.jsonl` | 29,100 | `7ca035c170cb324a0e590b6f47e2bc7b7237fbeaf2d2a7659daaf2b819a2c698` | post-power-cycle unit-0 retries |
| `/tmp/growatt-ha-dev2c-raw-forensic.jsonl` | 179,665 | `b781c84a95ca3d8407a646683a86ec20c993fb427b77406af8f6aca678c4afbb` | copied forensic provenance capture |

The zero-byte file `/tmp/growatt-ha-dev2c-raw-20260907-184336.jsonl` is an
unused capture attempt and is not evidence. A live-host check found the raw
container stopped and did not alter the original captures; only the explicit
forensic copy had been placed on the RPi. Full raw captures are intentionally
not committed.

Evidence labels used below are:

* **LIVE DEVICE** — bytes or values observed on the live serial path;
* **VENDOR** — Growatt documentation or the resolved project register map;
* **LOCAL RE** — existing `rev_eng*.txt`, `build_sample_sets.py`, and
  `shine_sample_sets.json` derived from earlier live captures;
* **INFERENCE** — a reproducible hypothesis that still needs an independent
  observation.

## Reconstruction method

The existing `analyze_sniff_log.py` and `analyze_forensic_rx.py` were run as
baseline checks. They intentionally do not parse raw `WIRE` events, so they
cannot recover these conversations. The dedicated
`tools/analyze_raw_wire.py` parser is therefore limited to this concrete gap.

It concatenates consecutive chunks in one direction, parses requests only at
their observed 8-byte boundary, parses FC03/FC04/FC20 responses from their
declared byte count, and requires a valid Modbus CRC. Responses are associated
only when direction, unit, function, and expected word count agree. It does
not search arbitrary offsets for lucky CRC matches and does not reinterpret a
unit-1 response as a unit-0 response. Unparsed boot/debug bytes and ambiguous
associations remain unclassified.

## Healthy request and response inventory

The primary capture contains 367 WIRE events: 48 Shine-to-inverter chunks and
319 inverter-to-Shine chunks. The parser recovered 48 valid requests and 46
complete CRC-valid responses. Two requests had no matching response and were
followed by a Shine retry.

| Request | Count | Returned response | Classification |
| --- | ---: | ---: | --- |
| unit 1 FC20 start 0 count 100 | 7 | 7 × 205 bytes / 100 words | healthy steady-state |
| unit 1 FC03 start 180 count 20 | 7 | 7 × 45 bytes / 20 words | healthy steady-state |
| unit 1 FC03 start 0 count 125 | 7 | 7 × 255 bytes / 125 words | healthy steady-state |
| unit 1 FC03 start 3000 count 125 | 7 | 7 × 255 bytes / 125 words | healthy steady-state |
| unit 1 FC04 start 3000 count 125 | 7 | 7 × 255 bytes / 125 words | healthy steady-state |
| unit 1 FC04 start 3125 count 125 | 8 | 6 responses, 2 no-response retries | 6 steady-state + 2 retries |
| unit 1 FC04 start 3250 count 125 | 7 | 7 × 255 bytes / 125 words | healthy steady-state |

All recovered frames had valid CRCs. The two missing I3125 responses are
observed transport outcomes; the parser does not convert them into synthetic
exception frames or claim an inverter exception code.

## FC20 structure

The exact request observed seven times is:

```text
01 20 00 00 00 64 81 e6
```

The exact response structure is proven by seven repeatable CRC-valid frames:

```text
01 20 c8 <200-byte payload> <2-byte Modbus CRC>
```

Thus the response is 205 bytes total: unit (1) + function (1) + byte count
`0xc8` (1) + 200-byte payload (100 big-endian 16-bit words) + CRC (2).
Every response, including its CRC and returned word count, is retained in the
JSON output. The payload is indexed independently from the Modbus register
address space as `word[0]` through `word[99]`.

### Word-level findings

The complete lossless table is in the JSON artifact. The following are the
useful review anchors; none except the frequency correlation is promoted to a
canonical semantic mapping.

| FC20 word(s) | Live observation | Current interpretation |
| --- | --- | --- |
| 1 | constant `0x008C` | constant field; semantic unknown |
| 3 | `0x08F6`–`0x08FA` | voltage-like changing candidate; LOCAL RE calls it DC-bus voltage, but no current standard-register equality proves that |
| 9 | constant `0x0010` | LOCAL RE calls it BDC buck/boost current; current capture alone does not validate the label |
| 15, 39 | same signed-changing values | duplicated/derived changing field; semantic unknown |
| 21, 41 | same changing values `0x09B1`–`0x09CD` | PV-bus/voltage-like candidate in LOCAL RE; not promoted |
| 27, 43 | same signed-changing values near `-2,500` | signed changing candidate; semantic unknown |
| 33, 45 | same signed-changing values | duplicated/derived changing field; semantic unknown |
| 47 | constant `0x01F4` (500) | `STRONG_CORRELATION` for frequency: contemporaneous canonical I3025 is 5000, which decodes to 50.00 Hz using its documented `/100`, while FC20 `500` requires `/10`; this is a scale/correlation hypothesis, not a canonical FC20 map |
| 55–57 | constant `0xD963 0001 4646` | stable metadata/counter-like region; not a meter attribution |
| other zero/constant words | mostly zero or fixed values | `UNKNOWN`: reserved, inactive, metadata, or unknown; no entity is inferred |

The parser also emits every adjacent pair as both unsigned and signed
big-endian 32-bit candidates. This preserves possible Growatt two-word
encodings without force-fitting a 32-bit semantic to a pair merely because it
changes.

The earlier LOCAL RE artifacts provide useful anchors such as words 3, 9, 21,
41, 47, 55, and 57, but they were made from older framed/log-derived sample
sets. Their names are therefore hypotheses/provenance, not a substitute for a
current independent register or passive-bus correlation.

The candidate classification is consequently: word 47 is
`STRONG_CORRELATION`; words 3, 9, 21, 27, 33 and their exact duplicate/copy
positions are `PLAUSIBLE` electrical candidates only; no word is a
`PROVEN_MATCH` to a meter field; and the remaining fields are `UNKNOWN` or
`NO_MATCH` for the tested standard values. A single short, mostly-idle capture
is not sufficient to promote any of these to a semantic key.

## Correlation with standard Growatt polling

The same healthy capture contains ordinary Shine FC03/FC04 polls. The parser
retains all matched standard page words and records which standard responses
were temporally adjacent to each FC20 frame. The primary evidence shows:

* canonical FC04 I3025 was 5000, 5001, or 5002 in the repeated snapshots;
  canonical decoding gives approximately 50 Hz;
* FC20 word 47 remained 500 in all seven frames, matching that physical value
  only after the different apparent scale is applied;
* FC20 words 3, 21, 27, and their duplicated copies did not equal the
  contemporaneous canonical I3000/I3125 values for the candidate labels from
  the older RE artifact;
* standard I3041–I3046, I3171, I3173–I3179 and I3180–I3181 were captured for
  comparison, but the short mostly-idle window does not provide enough
  independent variation to assign the FC20 power/current words.

No P1 or Recorder database was modified or used as a live correlation source
in this offline task. The HA-8C inventory remains the available read-only
reference: P1 is a billing-point reference and was not assumed to be the
DDSU666. A future passive meter capture must compare P1, DDSU666 and Growatt
quantities at aligned timestamps.

## Is FC20 meter traffic?

**Not proven.** FC20 was originated by Shine and its payload contains changing
electrical-looking values, but the current capture does not identify a DDSU666
slave address, meter function, meter register range, or meter-specific
payload. The frequency candidate is an inverter quantity and is not evidence
of meter origin. The existing Growatt quantities suitable for later comparison
are:

* FC04 I3043–I3044: inverter-reported grid export;
* FC04 I3045–I3046: inverter-reported on-site load;
* FC04 I3041–I3042: inverter-to-user/load power;
* PV and battery power/current values from the native I3000/I3125 pages.

The installed model is still **CHINT DDSU666**, and Growatt support for MIN
2500–6000TL-XH is confirmed by vendor documentation. That identifies the
hardware, not the source of FC20 words.

## Reconstructing Shine discovery (H43)

The exact recurring discovery request in the post-replug and WiFi-rejoin raw
captures is:

```text
00 03 00 2b 00 01 f5 d3
```

This is unit 0, FC03, address H43, count 1, with valid CRC. The WiFi-rejoin
capture contains 60 such requests and no inverter response; the after-power
cycle capture contains 150 and no response. The hotplug capture also contains
the request amid raw reset/debug traffic, but does not establish a matching
response.

An earlier framed run displayed a nearby valid unit-1 response:

```text
01 03 02 13 ec b4 f9
```

It is a live observation of a unit-1 H43-shaped response, but it is not proven
to be the response to the unit-0 request. The raw capture deliberately shows
why proximity is insufficient: normal unit-1 FC03/FC04/FC20 traffic continues
on the shared bus. Therefore the discovery result is:

```text
UNRESOLVED
```

`0x13EC` is a candidate H43 value from the separate framed observation, not a
proven unit-0 discovery result. No device-type or model mapping is invented.

## Shine FC06 H188

The earlier live combined run observed and forwarded one Shine-originated
FC06 write to H188 with value 0. Vendor V1.24 identifies H188 as datalog
connect-server status, where 0 means success and 1 means failed. This is
consistent with Shine housekeeping and is not an agent-originated write.

It does not make arbitrary FC06 traffic safe. Any future combined mode needs a
semantic allowlist, exact request/response matching, an exclusive write lease,
and independent readback evidence. The current HA-DEV-2D task performed no
writes and does not declare the path write-ready.

## Native timing

For the healthy capture, request intervals were:

```text
minimum 0.500 s   p05 0.500 s   p25 1.499 s
median  1.500 s   p75 1.500 s   p95 1.501 s
p99     1.501 s   maximum 1.501 s
```

The normal Shine sequence is approximately 1.5 seconds between requests, with
approximately 0.5-second transitions. FC20 response latency was 7–17 ms in
the captured wire timestamps. The raw-transparent bridge imposed no
one-second scheduling or retry policy, so these intervals are Shine-native
observations. They do not show a Shine violation of the Growatt protocol: the
vendor V1.24 constraint is a minimum command period of 850 ms and a
recommendation of 1 second, while this experiment also exposes asynchronous
inverter-side traffic and retry transitions that require a future scheduler to
be designed around actual bus ownership.

## Architecture disposition

The evidence supports the following order of preference:

1. **Model C — no combined production Shine mode:** keep HA on the known-good
   broker and use a bounded raw bridge only for offline/controlled forensics.
   This has the clearest response ownership and the highest production value
   for EMS reliability.
2. **Model B — Shine-primary opportunistic injection:** technically closest to
   preserving Shine behavior, but still requires reliable hotplug, asynchronous
   traffic handling, starvation protection, and a proven discovery association.
   The current Shine runtime failure makes this unsuitable for production.
3. **Model A — common fixed scheduler:** the previous framed experiment showed
   queue waits and starvation pressure when Shine and HA share the single
   physical bus. A common scheduler also risks changing Shine's native timing.

No scheduler or transport rewrite is made here. The next implementation task
should use validated family-native block plans for HA and treat Shine as an
independent asynchronous participant, not as a source that can be safely
reduced to one-register transactions.

## Reproduction and final state

```bash
python3 tools/analyze_raw_wire.py \
  /tmp/growatt-ha-dev2c-raw-20260907-184343.jsonl \
  --json docs/data/fc20_word_analysis.json
```

The final live state was checked after rollback: no-Shine broker running,
production HA left in place, and dev HA able to receive valid unit-1
responses. The two pre-existing untracked broker logs remain untouched and
unstaged. This report and the dedicated parser are the only new forensic
artifacts; no raw capture was committed.

Safety totals for this task are: agent Growatt writes `0`; DDSU666 writes `0`;
production broker replacement `0`; production HA upgrade/restart `0`.
