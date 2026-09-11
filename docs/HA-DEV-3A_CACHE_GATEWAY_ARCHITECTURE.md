# HA-DEV-3A cache-centric Growatt broker architecture

Status: opt-in implementation; production remains on the legacy default.

## Decision

The broker should become a cache gateway with one physical inverter owner.
Shine, production HA, development HA, and tools should consume coherent raw
register snapshots instead of competing for independent serial transactions.
The Shine serial connection becomes a virtual-inverter client interface. Its
discovery request is terminated locally for a validated device profile, and
normal reads are answered from the same register cache used by HA.

The cache implementation is enabled only with `--mode cache`, `--mode cache+shine`,
or `--mode cache+shine-direct`. The default remains `--mode legacy`, so no
production
mode changes merely by upgrading the image.

## Evidence from HA-DEV-2C/2D/2E

- Raw Shine captures repeatedly showed the exact unit-0 H43 request
  `0003002b0001f5d3` after reset/reconnect, without a captured physical
  response.
- The healthy raw capture showed complete 8-byte Shine requests; it did not
  prove request fragmentation. Large inverter responses naturally arrived in
  multiple chunks.
- The historical framed observation contained
  `01030213ecb4f9`, followed in later sessions by normal unit-1 polling.
- HA-DEV-2E showed that a no-flush bridge could coincide with Shine leaving
  discovery, but the decisive first response was not retained and no causal
  flush conclusion was possible.
- A clean HA-DEV-3A live proof now supplied the missing local experiment:
  after one synthetic `01030213ecb4f9`, the Shine emitted normal unit-1 FC03
  requests. See the separate live report.
- Observed normal workload includes FC03 pages, FC04 I3000/I3125/I3250
  pages, and the exact opaque FC20 request
  `01200000006481e6`. FC20 is not established as a DDSU666 proxy.

## Ownership invariant

Exactly one component, `PhysicalPoller`, may open `/dev/inverter`. It is the
sole physical Modbus master and the only component allowed to perform physical
reads or reviewed writes.

The following interfaces never open `/dev/inverter`:

```text
ShineVirtualInverterAdapter -> cache / demand coordinator
HA production adapter       -> cache / demand coordinator
HA development/tools        -> cache / demand coordinator
```

Raw-transparent passthrough remains useful as an explicitly bounded forensic
mode, but it is not the normal combined architecture.

## State machine

```text
SHINE_ABSENT
    | hotplug
    v
SHINE_RECOVERING/DISCOVERY -- exact profiled H43 --> SHINE_PRESENT
    ^                                               |
    | disconnect                                    | normal requests
    +---------------- SHINE_LOST <-----------------+
```

`SHINE_ABSENT` and `SHINE_LOST` keep autonomous polling alive. In
`SHINE_PRESENT`, Shine requests are observations and demand signals; they do
not acquire physical ownership. A discovery request is recognized only when it
exactly matches a device-scoped `DiscoveryProfile`; in the transparent canary
it is physically passed through so the inverter remains authoritative.

## Raw register cache

The cache key is a physical contiguous range:

```text
RegisterKey(function, start, count)
```

Each successful physical block creates a `RegisterSnapshot` containing:

```text
key                 FC03/FC04 and physical range
words               immutable unsigned 16-bit values
captured_at         monotonic freshness timestamp
generation          deterministic cache generation
snapshot_id         acquisition-batch identity
source_transaction  physical transaction/audit identifier
quality             GOOD, FAILED, or UNKNOWN
```

This is intentionally below semantic registers and Home Assistant entities.
Unused words in a native page remain cached but do not become HA entities.

The first implementation is `RegisterCache`. It refuses stale or failed data
as fresh data. A request contained in one fresh block is sliced locally. A
range crossing blocks is composed only when every piece is fresh and all
pieces share the same `snapshot_id`; otherwise a refresh is required. This
prevents silently combining unrelated generations.

The live implementation polls the validated native MIN/TL-XH blocks as
background work and serves contained TCP or virtual-Shine reads by slicing
those snapshots. The current plan is exposed by
`native_min_6000tl_xh_plan()`; it intentionally does not turn unused words
into entities or issue one physical transaction per entity.

The cache mode can be exercised with:

```text
--mode cache
--mode cache+shine --shine /dev/serial/by-id/<shine-port>
--mode cache+shine-direct --shine /dev/serial/by-id/<shine-port>
```

`cache+shine` recognizes the validated unit-0 discovery request but forwards it
through the physical passthrough, answers standard reads from the shared cache,
replays a validated FC20 object from the same service, and forwards writes and
other valid unknown requests physically. Valid unsolicited inverter frames are
forwarded to the Shine. Bytes that do not form a CRC-valid frame are detected
and reported (`shine_unframed_bytes` or `inverter_unframed_bytes`) with a
bounded preview; they are not forwarded. Legacy and raw-transparent modes
retain their existing behavior.

`cache+shine-direct` keeps the cache available for HA/TCP clients but makes the
Shine serial endpoint fully transparent: every valid Shine request, including
discovery, FC20, writes, and unknown functions, is sent to the inverter and the
physical response is returned to the Shine. No Shine request is answered from
the register or FC20 cache. The cache is still populated by HA/TCP reads and
is available to those clients. This mode is intended for validating Shine
behavior that depends on coherent, current inverter registers such as clock or
status fields.

## Native block polling

The physical planner must optimize transaction count, not word count. The
initial MIN/TL-XH profile is based on observed/vendor-native blocks:

```text
FC03 H0       count 125
FC03 H3000    count 125       where applicable
FC04 I3000    count 125
FC04 I3125    count 125
FC04 I3250    count 125       where applicable
FC03 H180     count 20        observed smaller request
FC03 H209     count 15        observed smaller request
FC20         exact opaque request, not a register block
```

Complete vendor-native pages are preferred when hardware validation confirms
them. Smaller reads are a planner decision for a demonstrated frequency or
device-applicability reason, not an entity-driven default. The vendor V1.24
timing evidence remains part of the family profile: 850 ms minimum command
period and 1 s recommendation, with the applicable maximum read length and
native page layout. The poller must not turn these into global constants for
older protocol families.

The future physical interface should be explicit:

```python
read_block(PhysicalPollRequest) -> RegisterSnapshot
read_fc20(request)             -> OpaqueProtocolObject
write_single(...)              -> exact acknowledged result
write_multiple(...)            -> exact acknowledged result
```

No one-register-per-entity behavior is encoded here.

## Freshness and client semantics

The gateway applies a per-block/per-poll-class freshness deadline:

1. Fresh cache: synthesize the client response immediately.
2. Refresh in flight: attach the client to that physical refresh.
3. Stale cache: issue one refresh and satisfy all waiters from the new
   snapshot.
4. Physical failure: return an explicit failed/timeout result. Stale data is
   never labelled current; any stale-grace policy must be separately and
   visibly configured.

`PollCoordinator` implements this contract synchronously as a deterministic
prototype. A later async transport can use the same ownership and completion
interfaces.

## Coalescing

Demand is represented by `ClientReadRequest`, while physical work is a
`PhysicalPollRequest`. A demand for a subset of an already in-flight larger
block joins that request. Duplicate simultaneous demands also join it. On
completion, each client receives an independently synthesized view of the one
cached physical block.

The concrete target for the observed example is:

```text
t=0.000  Shine wants I3000..3124
t=0.020  HA wants I3000..3124
t=0.040  DEV wants I3020..3039

physical work: one FC04 I3000 count 125
client work:   three local responses
```

## Shine synchronization

`ShinePatternObserver` records a bounded sequence of `(function, start,
count)` requests and transition intervals. It uses a median interval and a
jitter bound, not exact phase locking. A learned sequence is used only while
the order and timing remain credible. A changed sequence clears prediction and
falls back to freshness/on-demand behavior. When Shine disappears, the
autonomous default profile continues.

The adaptive poller should refresh the predicted native block shortly before
Shine asks for it. It must never issue a second physical read merely because
the Shine asked for a range that is already fresh.

## Shine absent/present behavior

When absent, the physical poller uses a conservative Shine-like profile built
from evidence: the native FC04 pages, observed FC03 pages, and an explicitly
scheduled FC20 primitive. It does not blindly replay undocumented writes.

When present, Shine requests are observed, cached, and used to refine order,
cadence, and prefetch. The Shine receives synthesized responses from cache.
It does not become a second physical master.

## Discovery handling

The live device-scoped profile used in HA-DEV-3A is:

```text
device:  MIN 6000TL-XH / validated live profile
request: 00 03 00 2b 00 01 f5 d3
response:01 03 02 13 ec b4 f9
```

The prototype stores this as `DiscoveryProfile`; it is not a global Growatt
constant. General production use requires a validated profile, learned and
persisted identity, or a startup-established identity before a synthetic
response is enabled.

## FC20

FC20 is represented by `OpaqueProtocolCache`. It stores the exact CRC-valid
request, exact CRC-valid response, capture timestamp, generation, and quality.
It does not decode the 200-byte payload and does not describe it as DDSU666
telemetry. A physical poller may later issue the exact known request and cache
the response, after an isolated read-only validation.

## Writes

FC06 and FC10 are not cache reads. They require a separate serialized write
path with:

- exact acknowledgement matching;
- no blind retry after an ambiguous timeout;
- affected-cache invalidation and read-back where applicable;
- origin, lease/arm state, and audit logging.

Shine-originated writes are classified independently as known housekeeping,
configuration, or unknown. The prototype quarantines FC06/FC10 by default and
does not decide that H188 should be forwarded. Unknown functions are also
quarantined. No write policy was enabled live.

## Failure semantics

| Condition | Required behavior |
|---|---|
| inverter offline | physical poll fails; clients get explicit unavailable/timeout |
| Shine absent | autonomous default polling continues |
| Shine hotplug | virtual adapter reconnects; physical owner unchanged |
| Shine discovery | exact profiled request answered locally |
| Shine normal polling | reads served from cache or coalesced refresh |
| Shine silent | decay prediction confidence; default polling continues |
| cache stale | one shared refresh, never silently current |
| client disconnects | remove its waiter; physical poll may still serve other waiters |
| physical timeout | complete waiters as failed; no false fresh snapshot |
| FC20 timeout | leave opaque object stale/unavailable; no fabricated payload |
| ambiguous write | do not blind retry; require audit/read-back policy |
| broker restart | empty cache; no values are served as initialized state |

## Transaction savings estimate

The HA-DEV-2C combined raw window recorded 48 Shine requests and 5 production
TCP requests in roughly 130 seconds. In that architecture those requests
occupied the shared physical path, with retries and queue waits. The capture
does not justify an exact future transaction count because freshness periods
and semantic polling classes have not yet been selected.

It does establish the useful bound: for every freshness interval, repeated
requests for one native page can change from one physical transaction per
client/request to one physical refresh shared by all clients. The deterministic
example above is a 3-to-1 physical reduction. The larger reduction depends on
which of H0/H180/H209, I3000/I3125/I3250, and FC20 are assigned FAST, NORMAL,
SLOW, and DIAGNOSTIC freshness classes.

## Prototype and validation

The prototype files are:

- `growatt_broker/cache_gateway.py`
- `tests/test_cache_gateway.py`

The focused suite contains 15 tests covering discovery scoping, fresh-cache
serving, overlap, stale refresh, duplicate coalescing, failure state,
generation coherence, state transitions, jitter/fallback prediction, opaque
FC20, write quarantine, and non-default behavior.

Existing non-simulator broker tests passed: 62 passed. The complete suite had
9 failures in pre-existing simulator tests because the installed PyModbus
version rejects the simulator's zero-based `SimData(address=-1)` setup. Those
failures occur before any cache-gateway behavior and are unchanged by this
task.

## Staged migration

1. Preserve the known-good no-Shine production broker.
2. Expand offline cache/coalescer tests and async completion interfaces.
3. Implement a sole-owner physical poller serving HA from native blocks, with
   no Shine connection.
4. Add virtual Shine discovery and read-only FC03/FC04 cache responses.
5. Add opaque FC20 physical polling and replay after isolated validation.
6. Add adaptive Shine cadence synchronization and freshness classes.
7. Review and implement explicit Shine housekeeping/write policy.
8. Run a bounded combined canary with rollback and persistent evidence.

No production migration is part of HA-DEV-3A.

## Open questions

- Which exact native blocks belong to FAST/NORMAL/SLOW/DIAGNOSTIC for this
  firmware and for older protocol families?
- What response latency and cache age does Shine tolerate for each block?
- Does a future validated profile need more than H43 for device identity?
- Which FC20 cadence is useful without duplicating Shine's proprietary work?
- Which H188 behavior is housekeeping and which is configuration on each
  Shine firmware version?
- How should HA entity/statistics continuity constrain any later source-map
  migration?
