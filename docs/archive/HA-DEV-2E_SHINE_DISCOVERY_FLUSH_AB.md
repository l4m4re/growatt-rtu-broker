# HA-DEV-2E Shine discovery flush A/B test

Date: 2026-09-08
Device: live `MIN 6000TL-XH`, unit 1
Production broker: `growatt-broker-ha2c-dfb99a8`
Disposition: **GREEN WITH FOLLOW-UP**

## Scope and safety

This was a bounded transport experiment. The known-good no-Shine broker was
stopped only for the isolated serial test. The temporary bridge exposed no TCP
ports, had no request queue, generated no retries, and did not impose a
one-second delay. It only opened the Shine and inverter serial adapters and
forwarded bytes.

No inverter power-cycle or Shine reset was performed. HA Core was not restarted
and no Growatt configuration write was issued by the agent. The production
broker was restored immediately after the test and returned to normal unit-1
telemetry.

## Baseline before the test

The Shine USB adapter was observed receive-only before changing the broker.
Six complete discovery requests were recorded at approximately two-second
intervals:

```text
2026-09-08T07:32:15Z  00 03 00 2b 00 01 f5 d3
2026-09-08T07:32:17Z  00 03 00 2b 00 01 f5 d3
2026-09-08T07:32:19Z  00 03 00 2b 00 01 f5 d3
2026-09-08T07:32:21Z  00 03 00 2b 00 01 f5 d3
2026-09-08T07:32:23Z  00 03 00 2b 00 01 f5 d3
2026-09-08T07:32:25Z  00 03 00 2b 00 01 f5 d3
```

No other Shine request was observed in that baseline. This confirms that the
Shine was still stuck in the unit-0/H43 discovery phase immediately before
the A/B attempt.

## Temporary bridge

The test harness was supplied to the isolated container over stdin and was not
added to the repository. It opened both adapters at `115200 8N1`, used
`select()` for bidirectional reads, and logged monotonic timestamps. For each
Shine-to-inverter write it recorded requested length, `write()` return value,
write timing, and whether the variant called `flush()`. Every inverter-to-
Shine write used `write()` followed by `flush()`.

The harness captured all inverter RX chunks without unit, function, CRC, or
normal-response filtering. It stopped before forwarding a natural Shine FC06
or FC10 write after progression. It did not generate retries.

## A/B result

The first variant-A run used the current raw behavior: `write()` without an
explicit flush on Shine-to-inverter forwarding. It ran for the bounded
interval and the Shine state changed from repeated H43 discovery to normal
polling. Unfortunately, that run wrote its JSON log inside the temporary
container; the Docker daemon's temporary filesystem was removed with the
container. Consequently its exact request count, response bytes, and timing
were not retained.

A repeat instrumented A invocation was then started, but by that point the
Shine had already progressed. It immediately emitted normal traffic and the
inverter returned data:

| Observation | Result |
| --- | --- |
| first observed Shine request | `01 04 0b b8 00 7d b2 2a` (FC04 I3000, 125 words) |
| subsequent requests | FC04 I3125/I3250 and FC03 H180/H0/H3000 pages |
| inverter RX | present, many complete 255-byte page responses and shorter FC03 responses |
| Shine-side write results | all retained writes were complete, e.g. 8 requested / 8 written |
| first retained inverter RX latency | approximately 17.7 ms from the first read request |
| natural FC06 observed | `01 06 00 bc 00 01 89 ee` (H188 value 1) |
| FC06 forwarding | stopped before forwarding by the safety guard |

The retained repeat therefore validates that the isolated bridge can receive
and forward normal inverter traffic, but it is not an A discovery trial: the
discovery state transition had already happened.

Variant B — `write(); flush()` on Shine-to-inverter forwarding — was **not
run**. Running it now would require resetting or otherwise disturbing the
Shine to recreate the discovery-only state, which this task explicitly
forbids. B cannot be interpreted causally after the state transition.

| Variant | Shine discovery requests in retained run | Full writes | Flush | Inverter RX | Shine progressed |
| --- | ---: | --- | --- | --- | --- |
| A initial, no flush | count not retained; bounded run caused a state transition | not retained | no | not retained | observed afterward, causal evidence incomplete |
| A repeat, no flush | 0; Shine was already progressed | yes, retained writes were full | no | yes | already progressed |
| B, explicit flush | not run | — | — | — | — |

## Interpretation

The result is `INCONCLUSIVE_DUE_TO_STATE_TRANSITION`, not evidence that flush
is required. The transition occurred during the no-flush A interval, so the
observation is compatible with A being sufficient, but the missing A log means
there is no retained proof of the exact H43 response or its latency. Because B
was not run from the same discovery state, no causal A/B comparison is valid.

The later instrumented A run did demonstrate that:

* normal Shine unit-1 polling was active;
* the inverter-side receive path delivered complete responses;
* all retained serial writes reported their full requested length;
* the bridge did not need to perform response matching for forwarding.

It did not establish whether explicit TX drain was responsible for the earlier
discovery transition. The exact first H43 response from the unlogged run is
therefore unknown.

## Production restoration

The temporary bridge was stopped and `growatt-broker-ha2c-dfb99a8` was started
again with the original configuration:

```text
inverter /dev/inverter @115200 8N1
TCP 5020 and 5021
sniff 5700
minimum period 1.0 s
```

Immediately after restoration, broker logs showed CRC-valid unit-1 FC03/FC04
responses to development HA clients, including the native I3000 and I3125
pages. Production HA was not restarted. The Shine remains physically present
but is not mounted by the production broker; the production broker owns only
the inverter adapter.

## Safety counters

```text
agent-originated Growatt writes       0
retained Shine FC06/FC10 writes sent  0
HA-originated requests during test    0
DEV-originated requests during test   0
inverter power-cycles                 0
Shine resets                          0
production HA restarts                0
```

No raw capture is committed. The report records the bounded baseline, the
instrumented post-transition evidence, the lost-log limitation, and why a
second flush variant cannot be run honestly without violating the requested
no-reset constraint.
