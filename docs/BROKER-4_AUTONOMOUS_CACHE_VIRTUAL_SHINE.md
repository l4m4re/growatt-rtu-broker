# BROKER-4 autonomous cache and bidirectional virtual-Shine gateway

Date: 2026-09-11  
Repository: `l4m4re/growatt-rtu-broker`  
Branch: `feature/autonomous-cache-virtual-shine-20260911`  
Starting SHA: `ef325f00f6eb3ab3cc8b88b97844a7237fea02e4`

## Disposition

```text
BROKER_AUTONOMOUS_CACHE_ACCEPTED_WITH_FOLLOW_UP
```

The autonomous/cache and bidirectional write architecture is implemented and
covered by deterministic offline tests. Candidate deployment, hot-plug HIL
validation, and a bounded DEV_TCP write/read-back on the live inverter remain
follow-up work. The stable production container was not restarted, replaced,
or reconfigured during this task.

## Runtime architecture

`Downstream` remains the sole owner of the physical inverter serial port. TCP
`:5020` (`PROD_TCP`), TCP `:5021` (`DEV_TCP`), Shine (`SHINE`), and background
polling all enqueue work through that same scheduler. A physical transaction is
never interleaved with another request. Writes are selected ahead of ordinary
background work at a transaction boundary; the configured inter-command
period still applies.

The cache gateway now starts its autonomous poller in every cache mode, even
when the Shine is absent. Cache freshness is represented separately from
semantic/entity selection. A cached native page contains unused words as raw
values; they do not become Home Assistant entities.

### Current MIN 6000TL-XH plan

| Physical request | Service class | Target interval | Hard max age | Priority |
| --- | --- | ---: | ---: | ---: |
| FC04 I3000/125 | control | 10 s | 15 s | 0 |
| FC04 I3125/125 | control | 10 s | 15 s | 0 |
| FC03 H3000/125 | monitoring | 60 s | 180 s | 2 |
| FC03 H180/20 | monitoring | 300 s | 600 s | 5 |
| FC03 H209/15 | monitoring | 300 s | 600 s | 5 |
| FC03 H0/125 | diagnostic | 300 s | 600 s | 6 |
| FC04 I3250/125 | diagnostic | 300 s | 600 s | 6 |
| exact FC `0x20` request | opaque telemetry | 9 s | 15 s | scheduled |

The plan is transaction-oriented. A useful FAST snapshot consists primarily
of the two 125-word input pages I3000 and I3125: two physical transactions,
with local decoding of the selected values. FC20 is an exact opaque request,
not a normal register page. In a full all-due refresh, eight physical
transactions are scheduled (seven standard blocks plus FC20), so the vendor
recommended one-second command period implies roughly seven seconds between
the first and last command start before response/processing overhead. Slow
diagnostic pages are lower priority and cannot replace or indefinitely starve
the fast pages.

The plan reflects the V1.24 vendor transport characteristics already recorded
in the project: 850 ms minimum command period, 1 s recommendation, and
125-word maximum read length. These are protocol/family characteristics, not
an arbitrary broker-only throttle. Older protocol families must use their own
profile.

## Shine and FC20 behavior

The virtual-Shine path serves standard reads from the shared cache and learns
request timing from observed Shine requests. It has an exact opaque cache key
for:

```text
01 20 00 00 00 64 81 e6
```

This is function `0x20`, decimal function `32`; it is deliberately not
interpreted as Modbus decimal FC20 (`0x14`) and its payload is not mapped to
semantic registers. The autonomous scheduler refreshes it independently.
When a refresh fails, the last valid object may be served only as an explicitly
labelled stale fallback for at most 300 seconds; no failed response is cached
as valid.

Valid asynchronous inverter frames remain observable and are forwarded to the
physical Shine endpoint. Requests without a cache representation, including
discovery and unknown valid framed requests, retain the physical passthrough
route. The existing serial reopen and bounded framer diagnostics remain in
place.

The existing long capture was not stopped or modified:

```text
/tmp/growatt-winter-boundary-20260911-092431.jsonl
```

At the analysis snapshot, `tools/analyze_sniff_log.py` reported 37,638 Shine
requests, 37,397 responses, 482 timeout records, 241 CRC-bad records, and
18,280 uncommon-function records. The capture was still growing, so these are
not final totals. The CRC-bad count includes the broker's timeout-associated
empty response records; it must not be read as 241 physical wire CRC failures.
The prior bounded evidence also showed the natural Shine FC10 write to
H3040-H3041 followed by a confirming read-back, which is the design reference
for the shared write path.

## Bidirectional TCP writes (BROKER-4A)

Both TCP listeners route FC06 and FC10 through the same physical path as
Shine writes. A TCP success is returned only after a CRC-valid, request-matched
physical inverter response. The broker never synthesizes or cache-acknowledges
a write, and duplicate writes are not suppressed by the read-response cache.

After a successful physical write the overlapping holding snapshots are
invalidated immediately. The broker then reads back the smallest configured
native holding block that covers the address (for example H180/20 for H188 or
H3000/125 for H3038-H3059). Cache contents are populated only from that real
read-back. If read-back fails, the write result remains recorded as physically
successful but the affected cache remains unknown/stale and the TCP result is
labelled `physical_write_readback_failed`.

Reads arriving while the write/read-back lease is unresolved wait for the
sequence to finish; they are not served as fresh pre-write data. Physical
exceptions, timeouts, malformed responses, denied ranges, and invalid physical
responses produce meaningful Modbus exception responses. Asynchronous frames
cannot satisfy a pending standard write.

The initial profile-scoped TCP write policy is deliberately narrow:

```text
FC03/H188       count 1
FC03/H3038      count 22  (H3038..H3059)
```

Both `PROD_TCP` and `DEV_TCP` can be independently disabled with
`--prod-tcp-writes disabled` and `--dev-tcp-writes disabled`. The compose and
`docker/run_broker.sh` paths expose the same settings. This is transport
capability, not a declaration that every address is semantically or live
write-verified in the canonical register specification. The transparent Shine
path remains able to forward its own reviewed/observed writes, with source
tagging and shared serialization.

## Offline validation

The focused broker/cache/framing suite passed:

```text
python3 -m pytest -o asyncio_mode=auto -q \
  tests/test_broker_writes.py tests/test_cache_gateway.py \
  tests/test_tcp_transaction_cache.py tests/test_standard_framing.py \
  tests/test_shine_policy.py
95 passed
```

The tests cover FC06 and FC10, physical acknowledgement, native read-back,
cache invalidation, failed read-back, source policy, duplicate-write behavior,
concurrent reads, FC20 stale fallback, autonomous polling, fast-page
prioritization, and the same TCP gateway implementation for production and
development sources. Syntax/compile checks, `bash -n docker/run_broker.sh`,
and `git diff --check` also passed.

The repository-wide suite still has nine pre-existing simulator failures with
the installed PyModbus version: its current `ModbusSequentialDataBlock(0, ...)`
path constructs `SimData(address=-1)`. No broker/cache test failed. Fixing that
unrelated simulator compatibility issue is deferred rather than mixing it
into this gateway change.

## Live validation and remaining gate

No candidate image was deployed to the live Raspberry Pi in this checkpoint.
Consequently the following evidence is still required before an unqualified
acceptance:

1. run the candidate beside the established operational safeguards without
   restarting the stable production broker or inverter;
2. verify Shine absent, hot-unplug, replug, and reconnect state transitions;
3. observe natural Shine reads/writes and confirm source attribution,
   asynchronous forwarding, fast-page freshness, and no cache-miss storm;
4. perform one bounded safe DEV_TCP FC06/FC10 write within the allowlist,
   record the real physical acknowledgement and native read-back, then restore
   the original value;
5. verify that production HA and the Shine remain healthy throughout.

No HA entities, Recorder data, inverter configuration, production broker, or
long-running capture was changed by this task. The two existing untracked
research logs remain untracked and untouched.

Implementation SHA: `78832e6`  
Final SHA before remote publication: `8cbf9ea`
