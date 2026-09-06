# HA-DEV-2B transport and physical RX forensics

The TCP retry cache is a same-connection duplicate guard. An exact duplicate
is defined by connection-local state plus the complete tuple
`TID + PID + unit + PDU`. Within the short bounded cache window it is
suppressed after the original response has been sent; it is not replayed. A
different TID, PDU, connection, or expired entry remains an independent
request.

This is safe for an already-served retry because the original response was
sent over the same live TCP connection. TCP provides ordered reliable delivery
for that response. The application retry is a timeout reaction to slow broker
service, not a TCP retransmission requirement. The rule does not apply to
independent requests or to a new connection.

## Forensic mode

Raw inverter-side capture is disabled by default. Enable it explicitly on a
bounded research run:

```text
--forensic-rx-log /tmp/growatt-forensic.jsonl
--forensic-rx-max-bytes 10000000
--forensic-rx-max-seconds 1800
```

The broker remains the only serial owner. The JSONL file records physical TX
requests and every serial RX read with wall-clock and monotonic timestamps.
The sources include pre-request drains, normal framer reads, timeout residuals,
leftover framer bytes, and an explicit post-test drain. Buffer snapshots are
marked separately and are not counted twice as newly read bytes.

After the read-only run, request a final drain without sending a frame:

```text
kill -USR1 <broker-pid>
```

Analyse the result outside Git:

```text
python tools/analyze_forensic_rx.py /tmp/growatt-forensic.jsonl
python tools/analyze_forensic_rx.py /tmp/growatt-forensic.jsonl --json \
  > /tmp/growatt-forensic-summary.json
```

The classifier only accepts structurally bounded known frames with valid CRC.
It labels expected standard responses, late standard responses, unsolicited
CRC-valid frames, and leaves unclassified bytes as unknown. A CRC-valid
substring alone is not treated as proof of a frame.

## Timeout budget

With the live candidate's `--rtimeout 1.5` and two broker-owned attempts, one
physical transaction can consume up to approximately 3.0 seconds. A request
already ahead of a development request can therefore make a bounded one-ahead
service time approximately 6.0 seconds, in addition to scheduling jitter.
The global one-second command spacing is enforced across all clients, not per
TCP listener.

The development HIL client should therefore use a timeout greater than that
bound, for example 8 seconds, and `retries=0` while the broker owns physical
retries. Production Home Assistant timeout settings are outside this broker
change and require a separate review. Duplicate suppression remains defense in
depth for clients that still retry.

## Existing reverse-engineering provenance

The local `rev_eng.txt`, `rev_eng_full.txt`, and `rev_eng_words.txt` files,
plus `tools/reverse_engineer_growatt.py` and
`tools/build_sample_sets.py`, contain prior Shine-side function `0x20`
analysis. `docs/data/shine_sample_sets.json` contains derived samples. This is
third-party/local reverse-engineering material, not live proof for the MIN
6000TL-XH. The forensic run must keep any observed `0x20` frame explicitly
separate from that hypothesis and must not label it DDSU666 data without
independent correlation.
