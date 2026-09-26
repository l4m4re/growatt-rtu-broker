# Handoff to Growatt_ModbusTCP developers

This document separates PiITM transport evidence from inverter register
semantics. It is intended for review by the developers of
[Growatt_ModbusTCP](https://github.com/l4m4re/Growatt_ModbusTCP) and the
Growatt inverter information project.

## What PiITM provides

PiITM is a Raspberry Pi single-owner bridge:

```text
Home Assistant TCP 5020 ┐
Development TCP 5021   ├─ queued scheduler ─ physical inverter RS-485
Shine serial           ┘
```

The cache gateway stores coherent native FC03/FC04 blocks. Cache misses
refresh a complete physical block. FC06/FC10 writes invalidate overlapping
blocks and perform a paced complete-block readback before a coherent success is
reported. Raw-transparent mode is a separate bounded byte bridge with no TCP
endpoint.

The live X2 reference profile uses 115200 8N1, a 0.5 second minimum physical
transaction period, an 8 second RTU timeout, a 6 second FC20 timeout, and an
eight-transaction Shine burst. Both adapters are CH340-class; the X2 uses a
stable `/dev/serial/by-path` alias because it has no unique USB serial number.

## Reproducible release evidence

- source branch: `forensic/shine-firmware-update-20260925`
- source commit: `0cbd56ee50506b07d359cc2a2b8c2ae2db13fca9`
- CI: remote Python 3.11/3.14 tests, lint, and Docker build passed for this
  commit;
- live candidate image: `sha256:d37222ce729ec47caa57bd3245ded46ce8c5a1eab2ac934d109c48830dc606a8c`;
- live read-only canary and rollback:
  [`PIITM_LIVE_ACCEPTANCE_20260925.md`](PIITM_LIVE_ACCEPTANCE_20260925.md);
- compact protocol evidence:
  [`PIITM_PROTOCOL_AND_EVIDENCE.md`](PIITM_PROTOCOL_AND_EVIDENCE.md);
- historical reports and old logger observations:
  [`archive/`](archive/).

The canary ran with both TCP write policies disabled. It read `[1, 401]` from
both listeners, observed valid FC03/FC04/FC20 and Shine traffic, and restored
the existing `single-cache-fa3b694` container. One physical Shine timeout was
recorded; the broker exposed it as a timeout rather than fabricating success.

## Wire examples

These are parser/replay examples, not a universal register map:

```text
01 03 00 00 00 7d 85 eb       FC03 native holding page
01 04 0b b8 00 7d b2 2a       FC04 native input page
01 20 00 00 00 64 81 e6       opaque FC20 request for 100 words
01 20 00 03 00 2b f5 d3       historical discovery-related FC20 request
```

The evidence labels are `PROVEN_LIVE`, `REPLAYED`, `STATIC_ANALYSIS`,
`VENDOR_REFERENCE`, `HYPOTHESIS`, and `NOT_TESTED`. The broker transports FC20
as an opaque primitive; this repository does not assign meanings to its words.

## What belongs in Growatt_ModbusTCP

Please keep the following out of PiITM and record them in the register
authority with model, firmware, source, and confidence:

- names, scaling, signedness, access flags, and units for H/I registers;
- FC20 word semantics and whether the payload is device-family specific;
- H188/H209 and discovery behavior;
- VPP register meaning and firmware availability;
- limits and effects of TOU, SOC, charge-rate, and discharge-rate writes;
- differences between inverter firmware generations and Shine-X versus X2.

PiITM can add a transport fixture or capture when a semantic hypothesis needs
reproduction. It should not copy a semantic map into the broker.

## Suggested next experiments

1. Replay the compact FC03/FC04/FC20 samples through the simulator and compare
   parser output with Growatt_ModbusTCP.
2. Capture one named inverter model and firmware with the same bounded profile,
   then correlate changed words with an independently controlled UI action.
3. Test one approved FC06/FC10 write with a documented rollback and compare
   physical acknowledgement, readback, and Shine visibility.
4. Record any VPP or firmware-update frames separately from normal Modbus
   traffic; do not infer a function code from raw bootloader bytes.

Full production captures, credentials, private addresses, and firmware images
stay outside this repository. The PiITM maintainers can provide a hash and
protected evidence location when a replay fixture is needed.
