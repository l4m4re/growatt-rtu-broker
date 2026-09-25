# Reverse Engineering Plan for Shine 0x20 Frames

This plan outlines discrete tasks an agent can execute to map the proprietary Shine 0x20 payloads to documented Growatt registers and integrate decoded values into the tooling.

## Phase 1: Data Extraction & Preparation
- [ ] **Assemble Sample Sets**: Collect representative `func=0x20` response frames and the corresponding TCP Modbus snapshots (`func=3/4`) covering varied operating conditions (charging, discharging, idle).
  - Dataset produced via `tools/build_sample_sets.py`; current export lives at `docs/data/shine_sample_sets.json` and subsequent runs append additional samples rather than overwriting.
- [ ] **Normalize Timestamps**: Build a helper that aligns Shine events with TCP reads (within ~1 s tolerance) to enable side-by-side comparison of register values versus payload word positions.
- [ ] **Baseline Stats**: Extend `reverse_engineer_growatt.py` (or a companion notebook) to emit per-word min/max, variance, and change frequency to prioritise likely telemetry fields.

## Phase 2: Field Correlation
- [ ] **Anchor Known Signals**: Confirm the correlations already observed (e.g., register 3025 ↔ payload word 47, registers 3002/3006 ↔ words 21/33, register 3173 ↔ word 3) using the synced dataset. Document scale/offset factors per field.
- [ ] **Map Remaining Words**: Iterate through remaining variable word indices, comparing against the documented register list (`doc/growatt_registers_best_guess.md`) to identify matches by range, dynamics, and scaling.
- [ ] **Reconcile Static UI Dump**: Load the saved Shine web settings capture (use `doc/showSettings.py` together with `external/Homeassistant-Growatt-Local-Modbus/doc/growatt_web/ui_metadata.json`, which maps UI elements to server keys) to connect configuration values with their on-wire representations and highlight UI-only fields.
- [ ] **Detect Redundant Blocks**: Determine whether the 205-byte payload contains mirrored segments (e.g., instantaneous vs. cached copies, per-phase repeats) and mark boundaries in the structure notes.

## Phase 3: Decoder Implementation
- [ ] **Design Payload Schema**: Draft a structured representation (e.g., dataclass or ordered mapping) describing each word/pair, its unit, scale, and register cross-reference.
- [ ] **Implement Parser**: Add a decoder module (e.g., `tools/decode_shine_frames.py`) that accepts a Shine frame and outputs typed fields using the schema. Ensure it gracefully handles variant lengths (204/205/88 bytes).
- [ ] **Integrate With Analyzer**: Update `reverse_engineer_growatt.py` and/or `analyze_sniff_log.py` to optionally annotate reports with decoded values, enabling quick validation during future captures.

## Phase 4: Validation & Tooling Enhancements
- [ ] **Cross-Check Against Live UI**: Compare decoded data with Shine web UI widgets (for power, battery, grid, temps, etc.) to verify correctness, capture any additional UI-only metrics, and backfill the field mapping table.
- [ ] **Regression Tests**: Create unit or property-based tests using captured frames to ensure the decoder remains accurate as tooling evolves.
- [ ] **Documentation**: Record the final field mapping, scaling, and assumptions in a dedicated doc (linking to the Homeassistant-Growatt-Local-Modbus register IDs) so future contributors can extend the work.

## Stretch Goals
- [ ] **Automated Drift Alerts**: Add a check that flags when Shine payload values deviate significantly from Modbus reads (e.g., due to firmware updates or communication issues).
- [ ] **Real-Time Export**: Expose the decoded Shine data via a lightweight API or log sink to let other tools consume the richer telemetry without waiting for Modbus polls.
