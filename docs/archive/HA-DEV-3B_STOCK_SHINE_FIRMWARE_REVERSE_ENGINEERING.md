# HA-DEV-3B — static reverse engineering of stock ShineWiFi-X firmware

Date: 2026-09-09
Scope: static analysis only; stock ShineWiFi-X-compatible ESP8266 images
Disposition: **GREEN WITH FOLLOW-UP**

## Safety and scope

The downloaded images were read but never executed as host programs. No Shine
was flashed, reset, opened on the serial device, or otherwise changed. The
production broker, inverter, Home Assistant, and live serial wiring were not
changed. The two pre-existing untracked broker logs remain untracked.

This report intentionally distinguishes evidence from the binary, earlier live
captures, vendor material, and open-firmware reference code. It does not use
static byte coincidence to manufacture a complete protocol map.

The compact machine-readable finding set is
[shine_stock_3_0_0_2_protocol_evidence.json](data/shine_stock_3_0_0_2_protocol_evidence.json).
The deterministic scanner is
[analyze_shine_firmware.py](../tools/analyze_shine_firmware.py).

## 1. Inventory

The source downloads are outside the broker repository's tracked files.

| Object | Size | SHA-256 | `file(1)` result |
| --- | ---: | --- | --- |
| `/workspaces/HA-core/external/bin/Growatt_Sunshine_flash_4M.bin.zip` | 317,735 | `01c79fbbf160fc201912206383741023dad2b3d917755e935d73496575b530f6` | Zip archive, uncompressed size 4,194,304 |
| `/workspaces/HA-core/external/bin/growatt.shine-wifi-3.0.0.2.bin` | 4,194,304 | `4b06602f07a03da07a53af256bda56e6d76e3cab258ec171731b3ab047814700` | DOS executable false positive caused by ESP header; not a DOS program |
| `/workspaces/HA-core/external/bin/growatt.shine-wifi-3.0.0.2.bin.zip` | 616,983 | `8aa4062950583cdfdc825ae917bfa3701b563d447b7ece4c1663eb32c8b8b366` | Zip archive, uncompressed size 4,194,524 |

ZIP contents:

* `Growatt_Sunshine_flash_4M.bin.zip` contains one 4,194,304-byte file,
  `Growatt_Sunshine_flash_4M.bin`. Its extracted SHA-256 is
  `718b31ccc3a7d51c1d3ec6101216b0d41a2b266a3d6c99963594c0f8ecee49ab`.
* `growatt.shine-wifi-3.0.0.2.bin.zip` contains the same 4,194,304-byte
  `growatt.shine-wifi-3.0.0.2.bin` as the loose file, plus a 220-byte
  `__MACOSX/._growatt.shine-wifi-3.0.0.2.bin` resource.

The two extracted 4 MiB images are not identical. The loose image and its ZIP
member are byte-identical. No extracted object, raw binary, archive, or large
disassembly was added to Git.

## 2. ESP8266 image structure

Both files are 4 MiB flash-layout images, rather than small application blobs.
The deterministic scanner and `esptool 5.4.0 image-info` found aligned ESP8266
image headers at offsets `0x000000` and `0x068000` in both files.

| Header offset | Segments | Entry | 4 MiB image | 3.0.0.2 image |
| ---: | ---: | --- | --- | --- |
| `0x000000` | 3 | `0x40100438` | QIO | DIO |
| `0x068000` | 3 | `0x40100004` | QIO | QIO |

The ESP image checksums reported by `esptool` are valid (`0xd8` for the first
header and `0x6f` for the header at `0x068000`). The scanner independently
validates the same padded checksum locations. The second header is therefore a
real additional image slot or firmware image, not a coincidental `0xe9` byte.

The primary application content is effectively shared through offset
`0x081000`. The first broad difference is `0x081000–0x0ec4ff`, followed by
small configuration/metadata differences near the end of flash. The 4 MiB
image has `3.0.0.0` at `0x70453`; the named 3.0.0.2 image additionally has
`3.0.0.2` at `0xeb833`, in the differing region. This is consistent with a
shared primary code area plus different filesystem/configuration or image-slot
content. It is not enough to call one file a newer complete firmware dump solely
from its filename.

The header's three low-level segments do not describe every byte of the
flash-mapped application region. Consequently the report treats the 4 MiB
objects as flash layouts with application/IROM content, not as three-segment
files whose descriptor end is the application end.

## 3. Reproducible analysis

The committed scanner uses only the Python standard library. It accepts one or
more binary paths and reports SHA-256, aligned ESP headers, padded checksum
validity, printable strings, keyword matches, and exact byte-pattern offsets:

```text
python3 tools/analyze_shine_firmware.py --json image-a.bin image-b.bin
```

The scanner deliberately labels constants as locations only. It does not claim
that a `0x007d` hit is a Modbus count or that an isolated `0x20` byte is FC20.
The static Xtensa review used Debian's `binutils-xtensa-lx106` and
`xtensa-lx106-elf-objdump`; the disassembly was generated outside Git.
The follow-up Ghidra project was also kept outside Git. The raw application
mapping used for the recovered call paths is VMA `0x40200000`.

## 4. Strings and metadata

The primary image contains a coherent stock application with these
independent-looking areas:

* cloud endpoint string `server.growatt.com:5279` near file offset `0x02acd4`;
* WiFi station/AP, scan, reconnect, SmartConfig, and local HTTP UI strings;
* TCP client socket/connect/send/receive strings and separate
  `ESP_SERVER_CONNECTED` / `ESP_SERVER_DISCONNECTED` state messages;
* inverter task, inverter FOTA, collector parameter, and inverter-type
  initialization strings;
* `dataregistersinglegetsize`, `dataregisteraddr_1..4`, and
  `holdregisteraddr_1..4` diagnostic strings near `0x02e488–0x02e570`;
* CRC/Modbus-related diagnostics, including
  `s3062ModBusResponse bIndex = %d - bData = %x` at `0x0307b0`;
* raw TCP receive logging such as `recv data %d bytes` and separate read-end,
  read-fail, and send-success messages.

The embedded HTTP UI contains configuration fields for server address/port and
collector/inverter timing. The report does not reproduce any potentially
sensitive configuration values. A string scan of the supplied images did not
produce a credential or token that is needed for this analysis.

The strings are strong evidence that local inverter handling, cloud TCP, WiFi,
FOTA, and HTTP configuration are all first-class firmware subsystems. They are
not, by themselves, proof that each subsystem has an independent task or that
one state transition can never affect another.

## 5. Key Xtensa findings

### 5.1 CRC and frame construction

The routine at VMA `0x40246aa4` is a conventional Modbus CRC16 loop: it starts
from `0xffff`, XORs each byte, shifts eight times, and applies polynomial
`0xa001` when the low bit is set. Multiple protocol routines call it. This is
**PROVEN_BINARY** evidence of in-firmware CRC construction/validation, but it
does not identify the semantic purpose of every caller.

### 5.2 Discovery request: H43

The routine beginning at file offset `0x473d8` / VMA `0x402473d8` constructs a
six-byte request body field by field:

```text
byte 0: routine argument
byte 1: 3
byte 2: 0
byte 3: 43
byte 4: 0
byte 5: 1
bytes 6..7: CRC16 over bytes 0..5
```

This is **PROVEN_BINARY** evidence of an FC03/H43/count-1 discovery builder.
The routine argument is not a literal in this function, so the binary alone
does not prove that it is always zero. The live capture supplies that
correlation: the Shine sends `00 03 00 2b 00 01 f5 d3`.

The same path reads a response, derives the expected data length from the
response count, validates the response CRC, and combines response bytes 3 and
4 into a 16-bit device-type value. In the range mapping immediately afterward,
the literal `0x13ec` is referenced at VMA `0x40247546`. The `0x13ec` value is
therefore **STRONG_CORRELATION** with the device-profile selection path.

It would be too strong to say that this function simply accepts “unit 1 plus
H43 equals 0x13ec”: the visible code does not directly compare the response
unit to 1, and `0x13ec` is used as a mapped internal profile value in a range
selection. The earlier live `01 03 02 13 ec b4 f9` remains valuable live
evidence, but its association with a unit-0 request is not independently
proven by the raw capture.

### 5.3 Profile selection and native pages

The initializer at VMA `0x40247068` is the most useful static table-like
finding. It branches on internal type values and writes a profile descriptor.
The branch for `0x13ec` shares the branch for `0x0bb8` and writes, among other
fields:

```text
start 0x0bb8 (3000)
start 0x0c35 (3125)
count 0x007d (125)
```

Other branches contain `0x14b4` (5300), `0x0465` (1125), `45`, `90`, and
additional profile selector bytes. The copied descriptor at `0x40247644`
contains two start/count pairs and selector bytes; the exact field names and
FC03/FC04 meaning are not recoverable from this excerpt alone.

This is a **PROVEN_TABLE** at the binary-structure level and
**STRONG_CORRELATION** with the live MIN/TL-XH FC04 pages I3000 and I3125.
It is not a claim that stock 3.0.0.2 polls every page in exactly the same order
as live 3.1.0.5.

### 5.4 Transition and H209

After a successful profile path, the code sets a profile/discovery state flag,
initializes the selected descriptor, sets additional state bits, waits with a
`0x3e8` (1000 ms) delay, and continues into the normal polling code. This is
**STRONG_CORRELATION** for a discovery-to-normal transition, not a full
recovered state diagram.

The routine at VMA `0x40247720` constructs:

```text
unit: routine argument
FC:   3
start: 0x00d1 (209)
count: 0x000f (15)
CRC:  computed by the shared CRC routine
```

With the normal live unit argument of 1, this is exactly the observed
`01 03 00 d1 00 0f 55 f7`. The routine validates the response CRC and waits
approximately 30 ms after a successful response. Failure paths use repeated
1000 ms waits. This makes H209 construction **PROVEN_BINARY** and its place as
the first normal live request **STRONG_CORRELATION**.

### 5.5 Poll reconstruction

| Order/state | Function | Start | Count | Evidence | Interpretation |
| --- | --- | ---: | ---: | --- | --- |
| discovery | FC03 | 43 | 1 | **PROVEN_BINARY**, live-correlated | H43 discovery builder |
| first normal candidate | FC03 | 209 | 15 | **PROVEN_BINARY**, **PROVEN_LIVE** correlation | H209 status/profile request |
| selected profile page | profile descriptor | 3000 | 125 | **PROVEN_TABLE**, live page correlation | descriptor value; function not independently decoded |
| selected profile page | profile descriptor | 3125 | 125 | **PROVEN_TABLE**, live page correlation | descriptor value; function not independently decoded |
| H180 | FC03 | 180 | 20 | **PROVEN_LIVE** only in current evidence | no direct static constructor isolated |
| H0 | FC03 | 0 | 125 | **PROVEN_LIVE** only in current evidence | no direct static constructor isolated |
| I3250 | FC04 | 3250 | 125 | **PROVEN_LIVE** only in current evidence | no direct static constructor isolated |
| FC20 | FC20 | 0 | 100 | **PROVEN_LIVE**; static 80-word path recovered | live 100-word range remains version/profile-specific |

The static code contains repeated 1000 ms waits and retry loops. It does not
prove the observed approximately two-second H43 cadence over hours. The
firmware may have additional scheduling around the routines, or the old image
may differ from the live 3.1.0.5 behavior.

## 6. FC20

The live evidence remains clear: Shine-originated `01 20 00 00 00 64 81 e6`
receives a CRC-valid `01 20 c8` response with a 200-byte payload. That is
**PROVEN_LIVE** and is already losslessly documented by HA-DEV-2D.

The first pass did not recognise the dynamic path. A follow-up Ghidra analysis
of the same supplied stock image recovered it:

* `FUN_40245aa0` dispatches an inverter operation descriptor with opcode
  `0x20` to `FUN_40243ba0`.
* `FUN_40243ba0` constructs a higher-level cloud/report object and reserves an
  `0xa0`-byte FC20 payload at offset `0x24`.
* It calls `FUN_40247a1c`, which explicitly writes function byte `0x20`, builds
  the inclusive start/count request, waits for a response, validates its CRC,
  and copies the response payload into the report object.
* The statically reachable example requests start `0` through `0x4f`, i.e. 80
  words. The live 3.1.0.5 capture requests 100 words. This is consistent with
  a version/profile-dependent range and is why the live count must not be
  generalized from the older image.

Therefore FC20 handling is now **PROVEN_IN_STOCK_IMAGE** for the supplied
image, and its inclusion in a cloud telemetry/report object is also proven for
that path. The exact 100-word layout and semantics remain separate questions;
the broker should continue to treat the live object as opaque, length- and
CRC-checked data until a version-matched map is established.

Nothing in the binary or live FC20 capture establishes that FC20 is a direct
DDSU666 proxy.

## 6a. Persistent re-upload storage and the five-minute question

The image contains a separate, explicit persistent backlog subsystem. The
strings `IOT_ESP_SPI_FLASH_ReUploadData_Read` and
`IOT_ESP_SPI_FLASH_ReUploadData_Write` resolve in the 3.1.0.5 Ghidra image to
`FUN_4023b1f0` and `FUN_4023b050`.

The writer and reader implement a CRC-protected circular SPI-flash store:

* the configured region starts at `0x00100000` and ends at `0x00500000`;
* records advance in 256-byte units and wrap at the end of the region;
* the reader requires a valid `01 ff` record marker, checks the requested
  length and payload CRC, checks the embedded 10-byte identity field, and then
  advances the read pointer;
* the writer stores an eight-byte record header, the payload, and its CRC, then
  advances the write pointer; rollover and full-region handling are explicit.

The 3.1.0.5 inverter task initializes the ring at `0x40237c3d`, stages a
record and calls the writer at `0x40237cbc`, and conditionally calls the reader
at `0x40237ce5`, when the re-upload state is enabled. This is strong static
evidence for a Shine-side offline cloud backlog, rather than a buffer that
exists only in the broker or portal.

The 3.1.0.5 staging path assembles a payload of `data_len + 16` bytes: a
10-byte time/identity candidate, six bytes of additional metadata, and
`data_len` bytes copied from the current telemetry/report buffer. The record
buffer is at `0x3fff24e0` and the report-data source used by this path is at
`0x3fff33fc`. The exact semantics of the 10-byte and six-byte fields remain
partly unresolved, but this is a structured report record rather than a raw
Modbus-frame FIFO.

The FC20 response is definitely embedded in the `FUN_40243ba0` cloud/report
object before network transmission. The report buffer is then copied into the
record staging area before `FUN_4023b050` writes it to the flash ring. This is
now **STRONG_STATIC_LINKAGE** between the FC20/report path and persistent
backlog storage. It does not yet prove that every stored record contains the
complete raw FC20 response byte-for-byte: the report builder may select,
reorder, or otherwise package fields before the copy.

### 6b. Report storage versus cloud-TCP encoding

The report/backlog copy and the cloud-TCP transport encoding are separate
stages in the 3.1.0.5 image. The flash-ring writer (`FUN_4023b050`) receives
the staged report payload directly and only adds its own eight-byte record
header, page padding, and CRC16. No AES or cloud-packet XOR routine is called
from that writer path. The persistent record should therefore be treated as a
binary structured report, not as an already encoded TCP packet.

The normal cloud send path is different. `FUN_40246710` performs the following
operations before the socket send:

1. `FUN_40239ee4(6, ...)` builds the cloud packet envelope and copies the
   report payload into it;
2. `FUN_4023a1b4(6, 0, ...)` applies a byte-wise XOR/stream transformation;
3. `FUN_4023a294(6, 0, ...)` appends the transport CRC;
4. `FUN_402597d4(...)` sends the resulting bytes over TCP.

The receive path applies the corresponding type-6 decode and CRC checks before
dispatching the data. This proves that the cloud TCP representation is not a
plain copy of the register/report bytes. The transformation is best described
as proprietary XOR/stream obfuscation; the analysed path does not establish
that it is cryptographically strong encryption or AES. The image does contain
generic AES source-name strings, but no call from the FC20/report-to-cloud path
to an AES routine was identified.

Consequently the current model is:

```text
inverter/FC20 data
        -> structured telemetry/report buffer
        -> persistent flash backlog (pre-cloud encoding)
        -> cloud packet envelope
        -> XOR/stream obfuscation + transport CRC
        -> TCP/WiFi
```

When a backlog record is replayed, the expected design is that the stored
structured report is passed through the same cloud send packaging before TCP
transmission. The exact reader-to-replay call chain remains a follow-up static
trace, so this last replay detail is not marked byte-level proven here.

The firmware also registers 300-second timer parameters, and the observed
five-minute portal samples/backlog are consistent with that scheduling. The
exact timer-to-ring-write association still requires correlation of the long
off-cloud capture with matching post-reconnect upload traffic.

## 7. H188 / FC06

The binary contains immediate value 188 at VMA locations around
`0x4023599f` and `0x402359fc`, in a collector/network data path that also does
buffer and length arithmetic. The surrounding code is not a clean FC06 frame
builder, so this is only **PLAUSIBLE** numeric overlap, not proof of H188
housekeeping.

The earlier combined live run observed one Shine-originated FC06 H188 write.
That is **PROVEN_LIVE**. Vendor V1.24's “datalog connect-server status” label
is consistent with it, but static firmware evidence does not prove the exact
trigger, failure policy, or whether all stock versions use the same value.
The cache gateway must continue to treat FC06/FC10 as policy-controlled writes,
not as ordinary cache reads.

## 8. Retry, reconnect, and state separation

The image has distinct diagnostics for:

* WiFi station/AP connect, disconnect, scan, reconnect, and RSSI;
* cloud TCP socket creation, connect success/failure, send/receive counters,
  long disconnect periods, and server-connected/disconnected transitions;
* local inverter task, inverter-type/profile initialization, CRC failure/success,
  and receive-management status;
* FOTA and local HTTP configuration/restart handling.

The code around H43 and H209 uses 1000 ms waits on retry paths, while the
network strings include a “reconnect after 1s” message. The stock image thus
supports a one-second retry building block, but not a complete proof of the
long-running H43 two-second cadence. The separate subsystems are
**STRONG_CORRELATION** for independent state handling; exact cross-trigger
behavior remains **UNKNOWN**.

This aligns with the live observation that WiFi/cloud can be online while local
inverter discovery is failing, and that the two can later diverge. The broker
should not tear down cloud state merely because the inverter-side cache is
temporarily unavailable unless a separate live observation proves that policy.

## 9. Comparison with live 3.1.0.5 evidence

The live reference is the accepted HA-DEV-2D/3A evidence, not a claim that the
live firmware is the same binary:

* H43 discovery bytes match the stock binary's field-by-field constructor.
* H209 FC03 address/count match the stock binary's isolated constructor.
* Live native pages I3000/I3125/I3250 use 125 words, matching the stock
  profile descriptor's 3000/3125/125 values.
* Live FC20 framing is repeatable, and a dynamic stock-image FC20 handling path
  is now recovered; the supplied image's 80-word example is not the live
  3.1.0.5 100-word range.
* Live FC03 H180/H0 and FC04 pages are not all statically reconstructed here.
* Live Shine timing is approximately 1.5 seconds in the healthy raw capture;
  stock code visibly contains 1-second waits but does not establish that exact
  cycle timing.

The strongest result is therefore a compatibility anchor for discovery,
profile selection, and at least H209—not a drop-in poll schedule for 3.1.0.5.

## 10. Open firmware reference

The public repositories were inspected as secondary reference only, without
cloning or flashing:

* [Alkhateb/Growatt_ShineWiFi-X](https://github.com/Alkhateb/Growatt_ShineWiFi-X),
  revision `32b9a6a6329d67984047d7ef37ba1db250ed3063`;
* [OpenInverterGateway/OpenInverterGateway](https://github.com/OpenInverterGateway/OpenInverterGateway),
  revision `a35045ea875045c0dea569dcc41da9d233f0f504`.

These projects confirm the expected ESP8266/CH340 ShineWiFi-S/X hardware
family, web configuration, Modbus polling, protocol-version-specific register
sets, and the fact that TL-XH uses a distinct 1.24-oriented register set in
the maintained open project. They are **OPEN_FIRMWARE_REFERENCE**, not proof
of stock Growatt behavior, and they were not used to fill missing FC20/H188
semantics.

## 11. Architecture feedback

1. **Does this support HA-DEV-3A?** Yes. The binary has device/profile
   selection and native page descriptors, while the live system has a single
   physical serial owner and repeatable Shine requests. A cache gateway can
   answer the known virtual-inverter reads without making Shine a second
   physical master.

2. **What can be answered from cache?** After a validated device profile and
   fresh snapshots: exact H43 discovery response, H209, H180, H0, native
   FC03/FC04 page slices, and other exact profiled reads. FC20 can be answered
   only as an opaque, exact, CRC-valid object with freshness/quality tracking.

3. **What needs profile-specific handling?** Discovery response mapping,
   function/table selection for the profile descriptor, page applicability,
   response lengths, and any H188 or other writes. Do not globalize the
   0x13ec/3000/3125 profile.

4. **Is FC20 suitable for opaque caching/replay?** Yes, if request shape,
   response length, CRC, generation, and freshness are checked. It is not yet
   suitable for semantic decoding or DDSU666 attribution.

5. **Which writes need policy?** FC06 H188 and every FC06/FC10 request that is
   not explicitly classified as Shine housekeeping. Require an allowlist,
   exclusive write lease, exact association, and audit/readback evidence.

6. **What timing should the broker imitate?** For Shine-absent operation,
   imitate the validated live profile only after preserving the vendor/broker
   transport constraints. The stock binary supports roughly 1-second retry
   waits; current live Shine evidence supports approximately 1.5 seconds
   between normal requests. Do not infer a complete cadence from the old image.

7. **What tests precede HA-DEV-3C?** Add deterministic tests for the exact
   H43 profile response, H209 construction/CRC, profile-dependent 125-word
   page selection, response-length/CRC rejection, opaque FC20 cache validity,
   absent/reconnect state transitions, and write-policy rejection for
   unapproved FC06/FC10. Add a replay fixture proving that one physical page
   can satisfy multiple Shine/HA slices without extra serial transactions.

## 12. Validation and publication

The focused analysis tests pass with the repository's standard-library test
runner:

```text
python3 -m unittest -v tests.test_analyze_shine_firmware
Ran 3 tests ... OK
```

They also pass through Pytest when the installed async-plugin compatibility
mode is selected explicitly:

```text
pytest -q -o asyncio_mode=auto tests/test_analyze_shine_firmware.py
3 passed
```

`python3 -m py_compile` passes for the tool and tests. The repository's
Pytest environment currently has an unrelated Home Assistant plugin mismatch:
the installed plugin registers an async `configure_event_loop` fixture that
Pytest 9 refuses for these synchronous tests. The focused tests were therefore
run through `unittest`; this limitation is recorded rather than changing the
shared test configuration for a static-analysis utility.

The final commit contains only the scanner, its focused tests, this report,
and the compact JSON evidence artifact. Raw firmware, ZIPs, extracted files,
temporary disassembly, and the pre-existing broker logs remain outside the
commit.

## Final disposition

### GREEN WITH FOLLOW-UP

Useful, reproducible stock-binary evidence was recovered and published. The
discovery builder, device-type/profile path, 3000/3125/125-word native profile,
H209 constructor, CRC handling, and independent-looking WiFi/cloud/inverter
areas materially support the cache-centric broker design. FC20 semantics,
the exact version-matched 100-word range, the association between FC20 report
objects and the persistent re-upload ring, complete stock poll ordering, exact
H43 acceptance/unit checks, and H188 trigger logic remain only partially
resolved and require later bounded live/replay work—not firmware flashing.
