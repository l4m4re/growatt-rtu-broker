# Live Growatt Broker Log Analysis Plan

## Objectives
- Derive reliable register/value mappings from the live Home Assistant broker logs and feed them back into the simulator and HA integration.
- Identify repeatable Modbus exchanges so we can simulate realistic traffic patterns and detect deviations faster.
- Build tooling that keeps future captures reproducible (same parsing code can cope with new logs).

## Log Characteristics
- JSON lines with `REQ` / `RSP`, per-frame CRC status, Modbus function, register range, and raw payload hex (e.g. `func=3` holding and `func=4` input reads).
- Requests arrive from two sources: Shine WiFi (`from_client="SHINE"`) and HA (`from_client="TCP:..."`). Responses follow the same shape with `to_client` fields.
- Repeated large payloads (125 register windows) indicate periodic polling sweeps; smaller, sporadic frames likely correspond to UI actions or configuration writes.

## Recommended Workflow
1. **Parse and normalise**
   - Convert each JSON line into structured objects grouped by transaction (`REQ` + matching `RSP` by timestamp ordering and `func/addr/count`).
   - Decode the response hex into register/value tuples (16-bit words, join to 32-bit where spec indicates).
2. **Classify patterns**
   - Bucket exchanges by function code, address window, and source (`SHINE` vs TCP client).
   - Track frequency and time deltas to distinguish baseline polling from manual operations.
3. **Compare against simulator datasets**
   - Diff observed register sets versus simulator definition JSON files to spot missing addresses or stale values.
   - Flag registers present in the simulator but never observed to confirm whether they are obsolete or simply absent from this device.
4. **Correlate with known UI actions**
   - Annotate timestamp ranges with contemporaneous user actions (if available) to link register changes to UI controls.
   - Record candidate read/write sequences for documentation and automation tests.
5. **Feed results back**
   - Update simulator datasets (`datasets/*.json`) with realistic baselines.
   - Capture new/changed register semantics in the register spec and HA integration tests.
   - Keep derived insights in versioned docs so the workflow is repeatable.

## Implementation Phases
- **Phase 1 (automation groundwork)**
  - Add a `tools/parse_live_log.py` helper that outputs CSV/JSON summaries: counts per `(func, addr)`, detected value ranges, and sample frames.
  - Provide filters (time window, source, function) to focus analysis.
  - Write unit coverage around the parser to lock in the log format.
- **Phase 2 (analysis outputs)**
  - Extend the tool to compare against simulator register definitions (`holding_*.json`, `input_*.json`).
  - Emit reports listing registers missing from definitions or values outside expected ranges.
  - Add optional export of replay datasets for the simulator.
- **Phase 3 (integration backports)**
  - Use the reports to update datasets and specs, create targeted simulator mutators for dynamic registers, and prepare HA integration PRs.

## Immediate Next Steps
- Implement Phase 1 parser with summary CLI (see `tools/parse_live_log.py`).
- Run it against `broker-210925.log` to produce baseline metrics we can iterate on.
- Capture findings in follow-up issues/docs as we uncover register behaviours.
