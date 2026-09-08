# HA-DEV-3B2 — ShineWiFi-X 3.1.0.5 diff and identity forensics

Date: 2026-09-08
Scope: static analysis of supplied flash images only
Disposition: **GREEN WITH FOLLOW-UP**

## Result

The authoritative dump is internally consistent and is a complete 4 MiB
ESP8266 flash image. It contains a coherent 3.1.0.5 application and useful
protocol-code continuity with the older stock images. The printed serial
`XGD6CCN109` is not present in the dump in ASCII, padded ASCII, UTF-16LE, or
UTF-16BE form; neither are `XGD`, `CCN`, or their case variants. The original
ESP MAC is present twice in the duplicated tail, but no firmware-supported
path from that MAC/chip ID to `XGD6CCN109` was found. Consequently the serial's
storage location and any cloud-provisioning secret are not proven.

The image is not sufficient to define a safe serial-only restoration patch for
Shine B. A read-only dump of Shine B, including its complete 4 MiB flash, is a
required follow-up before any restoration or identity operation. No identity
from Shine A must be copied to Shine B.

## Safety and provenance

Only files already present in the workspace were read. There were zero serial
opens, flash writes, erases, resets, inverter writes, broker changes, and HA
changes. The two pre-existing untracked broker logs were left untouched. No
binary, raw dump, disassembly, credential, WiFi password, token, or cloud
secret was added to Git.

Authoritative image:

```text
path:   /workspaces/HA-core/external/bin/ShineWiFi-X-3.1.0.5.bin
size:   4194304 bytes (0x400000)
sha256: b5e0644d03a33503812bd2770fde49762bd72e03dde3d92b5d68afc173
```

The image was independently read and hash-checked against the supplied value.
The comparison images were the extracted 4 MiB member of
`Growatt_Sunshine_flash_4M.bin.zip` (SHA-256
`718b31ccc3a7d51c1d3ec6101216b0d41a2b266a3d6c99963594c0f8ecee49ab`) and the
loose/ZIP-identical `growatt.shine-wifi-3.0.0.2.bin` (SHA-256
`4b06602f07a03da07a53af256bda56e6d76e3cab258ec171731b3ab047814700`).

## Identity and provisioning findings

The deterministic identity search examined exact and partial ASCII, NUL-
terminated ASCII, fixed-width NUL/space/`0xff` padding at widths 8, 10, 12,
16, 20, and 32, and UTF-16LE/BE. Results in the authoritative image:

| Candidate | Result | Evidence classification |
| --- | --- | --- |
| `XGD6CCN109` and case variants | no hits | not present as searched encodings |
| `XGD`, `CCN` and case variants | no hits | no binary string anchor |
| `ESP_74F8DE` at `0x3fd0b4` and `0x3fe0b4` | repeated in tail parameter areas | **STRONG_CORRELATION_HARDWARE_DERIVED** for an ESP-local identifier, not the Growatt serial |
| `e8:68:e7:74:f8:de` | raw bytes at `0x3fd484` and `0x3fe484` | **PROVEN_STORED** in duplicated tail; known ESP MAC, not the Growatt serial |
| `0x0074f8de` / `74f8de` | lower 24-bit bytes overlap the stored MAC at `0x3fd487` and `0x3fe487` | hardware-linked ESP-local correlation only |
| firmware/version/model strings | present in application/resource regions | **PROVEN_BINARY**, not per-device identity |

The repeated tail records at `0x3fd000` and `0x3fe000` are consistent with
duplicated ESP SDK/system-parameter storage. Their presence proves a
configuration/system-parameter area, not which fields are authoritative or
whether the records are checksummed. The two exact MAC records and the two
`ESP_74F8DE` strings make an ESP-local identity/default-SSID interpretation
strongly plausible: `74f8de` is exactly the lower three bytes of the supplied
chip ID and MAC. This does not establish that the Growatt serial is derived
from it.

## Hardware-derived serial investigation

The known identity anchors for the original Shine are ESP8266EX, MAC
`e8:68:e7:74:f8:de`, chip ID `0x0074f8de` (decimal `7665886`), and printed
Growatt serial `XGD6CCN109`. The extended static search tested raw and reversed
MAC bytes, compact/colon/dash ASCII MAC forms, 32-bit and 24-bit chip-ID byte
orders, six/eight-digit hexadecimal text, decimal chip ID, UTF-16 serial forms,
fixed-width serial padding, and serial partials. The result is:

| Search or path | Result | Interpretation |
| --- | --- | --- |
| Full MAC raw bytes | exact hits at `0x3fd484`, `0x3fe484` | stored per-device ESP identity in both tail copies |
| Reversed MAC and common ASCII MAC forms | no hits | no alternate representation found |
| Chip ID `0x0074f8de` | no independent 32-bit or decimal hit; `74 f8 de` is the MAC suffix | no serial construction evidence |
| `74F8DE` | hits only as `ESP_74F8DE` at `0x3fd0b4` and `0x3fe0b4` | supported hardware-linked ESP-local text identity |
| `6CCN109`, `XGD6CCN109`, `XGD`, `CCN` | no hits in searched forms | no serial anchor |
| `%02x`/`%02X` and MAC format paths | MAC formatting/logging strings and a six-byte formatting loop exist | proves MAC handling, not serial generation |
| `system_get_chip_id`, `wifi_get_macaddr`, eFuse/OTP symbols | no exported names or literal SDK symbol references | stripped/indirect ROM/SDK calls prevent a symbol-level proof |
| serial conversion path | no `%d`/`%u`/`%x` serial path, base36/base32 alphabet, XOR/offset routine, or product-prefix combination tied to the serial was isolated | no supported deterministic transformation to `6CCN109` |

The two MAC log strings are referenced by code at VMA `0x40242f54` and
`0x4024a93b`. The helper around VMA `0x4024a8e0` takes an interface selector
and output buffer, calls an SDK/internal MAC-getter-shaped routine at
`0x402058f0`, formats the six returned bytes as two-character hexadecimal
components, inserts colons, and passes the result to the diagnostic string
`IOT_ESP_wifiMACAddr_Get = %s`. A separate call site uses
`soft_AP_Init() wifi_get_macaddr = %s`. These are concrete station/AP MAC
handling paths. They do not pass the result to a routine or format string
that constructs `XGD6CCN109`, and the `IOT_ESP_InverterNewSN_Get` strings alone
do not prove where that value originates.

The only simple transformation supported by firmware evidence is the direct
uppercase hexadecimal lower-24-bit value in `ESP_74F8DE`; it explains an
ESP-local name and not `6CCN109`. Full-MAC, reversed-byte, decimal, 24/32-bit
raw, and ordinary hexadecimal forms do not produce the serial suffix. No
arbitrary base conversion or hash candidate is retained because no matching
firmware conversion path supports one.

### Serial-origin classification

**UNKNOWN**

This is deliberately not `PROVEN_STORED`, `PROVEN_DERIVED_FROM_HARDWARE_ID`,
`STRONG_CORRELATION_HARDWARE_DERIVED`, or `PROVISIONED_BUT_ENCODED`. The MAC is
proven stored and the ESP-local `ESP_74F8DE` value is strongly correlated with
hardware, but neither proves the Growatt serial's origin. An encoded or
external provisioning record remains possible without static evidence in this
dump.

## Button, SoftAP, and visible SSID investigation

The supplied live observation is that the physical button selects a
configuration hotspot whose SSID is reportedly `XGD6CCN109`, with remembered
address `192.168.10.100`. The SSID and address observation are treated as live
context; only the following static parts are firmware evidence.

### ADC/button side

The image contains `IOT_ESP_Key_Task` and the diagnostic format string
`system_adc_read = %d - %d -%d`. The key-task path is around VMA
`0x40237170–0x40237427`; it calls the small ADC-value accessor at
`0x40206708`, which reads the cached value at `0x3ffe8a28`, and logs it from
the key-processing loop around `0x40237466`. This is consistent with the
ADC-divider button design. The binary does not expose a symbol named for the
ADC pin or a clean, uniquely identifiable threshold branch that can be
followed all the way from one button level to SoftAP activation, so the
hardware ADC-to-button transition is **PARTIALLY_TRACED**, not proven end to
end.

### SoftAP/configuration side

The SoftAP initialization path around VMA `0x40242d5c–0x402430a9` contains
the following statically identifiable operations:

* wrapper `0x4024ab00` calls `0x402043a4`, a persistent WiFi-operation-mode
  setter shaped like `wifi_set_opmode()`;
* wrapper `0x40205c48` calls the SoftAP configuration setter-shaped routine
  with a 108-byte configuration object and logs
  `soft_AP_Init() wifi_softap_set_config = %d`;
* the initializer reads configuration-backed buffers, including a 10-byte
  flash/configuration read in the same path; it also formats a separate
  18-byte runtime MAC string before the SoftAP setup completes;
* `0x40205778` receives interface `1` and a four-word IP structure, then the
  caller logs `wifi_set_ip_info = %d`; `0x4020571c` follows in the same network
  setup sequence for DHCP/server state.

The literal `c0 a8 0a 64` occurs twice, at file offsets `0x42e48` and
`0x6cdbe`. At `0x42e48` it is represented in the instruction/literal pool as
`0x640aa8c0`, which is written little-endian into the IP structure. The
surrounding values include `ff ff ff 00` for the netmask and a zero gateway.
This is strong binary evidence for the remembered `192.168.10.100` SoftAP
address, but not evidence for the SSID's identity source.

The important result is the SSID source boundary: the SoftAP setter receives a
108-byte runtime configuration object whose surrounding initializer reads
configuration/flash-backed buffers. The clearest candidate sources are the
`0x3fa000` parameter area passed through helper `0x4024a33c` and the small
`0x5000`/dynamic ten-byte reads in the same initializer; neither can be
identified as a serial record from this stripped image. The current
disassembly does not provide
stable field names proving which object offset is the SSID, and it does not
show a proven copy of the formatted MAC into that field. No
`XGD6CCN109` literal, chip-ID-to-serial conversion, or product-prefix builder
is present in this path. The static image therefore cannot prove that the
visible SSID is generated from the MAC or chip ID. It is compatible with a
provisioned serial/config field (possibly encoded or loaded by a helper), and
also with a value obtained from another runtime subsystem. The
`IOT_ESP_InverterNewSN_Get` strings identify a serial-related getter/logging
area but no unambiguous caller-to-SoftAP data flow was recoverable from this
stripped image.

### Effect on identity classification and Shine B restoration

The button/SoftAP evidence does **not** change the serial-origin result:

**UNKNOWN**

The visible SSID is a valuable future behavioral anchor, but the current
static path proves only that SoftAP configuration consumes a runtime buffer.
It does not prove `PROVEN_DERIVED_FROM_HARDWARE_ID` or
`PROVISIONED_BUT_ENCODED`. A stock restore on Shine B may regenerate an
ESP-local AP name from B's own hardware identity, but it cannot currently be
expected to regenerate B's Growatt serial. Before restoring B, recover B's
complete flash, own MAC/chip ID, printed serial, duplicated configuration
records, and any separate provisioning/cloud material; then compare the AP
configuration path and observed SSID without copying Shine A's identity.

The dump contains no proven fixed-size record for `XGD6CCN109`, no proven
identity checksum, and no proven cloud token associated with that serial. The
binary therefore cannot establish that changing any one field would change
Growatt cloud identity safely. Sensitive-looking configuration content was not
reproduced.

## ESP flash layout

The ESP8266 image header at `0x000000` has three segments, entry point
`0x40100438`, QIO mode, and a valid checksum `0xd8`. The older images have
the same primary segment geometry and checksum; the 3.0.0.2 image reports DIO
for the primary header. Only the older images have a second valid aligned
header at `0x068000`, with entry `0x40100004`, three segments, and checksum
`0x6f`.

| Region | 3.1.0.5 observation | Interpretation | Confidence |
| --- | --- | --- | --- |
| `0x000000–0x06ffff` | nonblank primary image/IROM and application strings | primary application and embedded resources | high |
| `0x068000` | no valid aligned ESP header; application content is present here | part of the newer primary image, not a proven OTA header | high |
| `0x070000–0x0fffff` | overwhelmingly `0xff` | blank/unused or unavailable older OTA space | medium |
| `0x100000–0x13ffff` | nonblank code/data-like blobs; BMS/secondary-firmware strings around `0x125000` | embedded MCU firmware/resource payload candidate | medium |
| `0x140000–0x3f7fff` | overwhelmingly `0xff` | blank/unused flash | high |
| `0x3f8000–0x3fffff` | duplicated structured tail data and SDK-looking records | ESP SDK/system parameters and configuration candidates | medium |

The `0x100000` region is not called a filesystem without a filesystem
signature. Its ARM-looking code/data and BMS message names make an embedded
secondary-firmware/resource payload more plausible than ordinary web assets.
The exact partition table and write format remain unresolved.

## Deterministic diff map

The new `tools/diff_shine_flash.py` compares 4 KiB sectors, reports changed
bytes, entropy, printable strings, and coalesced intervals. The principal
results are:

| Comparison | Changed bytes | Changed sectors | Main intervals |
| --- | ---: | ---: | --- |
| 3.1.0.5 vs extracted 4M image | 498,126 (11.88%) | 172 | `0x001000–0x073fff` 68.25%; `0x100000–0x126fff` 88.07%; `0x132000–0x13bfff` 85.57%; tail `0x3f8000–0x3fffff` low-level changes |
| 3.1.0.5 vs 3.0.0.2 | 929,194 (22.15%) | 281 | primary `0x000000–0x073fff` 67.67%; old second image `0x081000–0x0ecfff` 97.35%; new `0x100000–0x126fff` and `0x132000–0x13bfff`; tail changes |

The older 3.0.0.2 image has a valid second application image at `0x068000`
and another nonblank image region from `0x081000`; 3.1.0.5 has neither valid
second header nor that old nonblank region. The older extracted 4M image has a
second header but its `0x081000` region is blank. This is evidence of different
flash packaging/layout generations, not proof that either old image is a
complete restoration source for the current device.

The primary application retains enough common structure for the protocol
findings below, but broad byte differences in the primary region mean that raw
offset copying is unsafe. The current image also adds the nonblank
`0x100000–0x13c000` payload area, which is absent from both comparison images.

## HA-DEV-3B protocol finding comparison

| Earlier finding | 3.1.0.5 result | Classification |
| --- | --- | --- |
| CRC16/Modbus routine | CRC16 routine with the same `0xffff` start and `0xa001` polynomial structure is present; literal/function placement differs | **SEMANTICALLY_MATCHING** |
| FC03 H43/count 1 discovery builder | routine at VMA `0x40244438` writes FC `3`, address `43`, count `1`, then CRC; response path validates CRC and extracts a 16-bit type | **SEMANTICALLY_MATCHING**; **PROVEN_BINARY** in current image |
| `0x13ec` profile selection | current table/initializer around VMA `0x40243f18–0x402441a1` contains the `0x13ec` branch | **SEMANTICALLY_MATCHING** |
| 3000/3125/125 descriptor | current initializer contains `0x0bb8`, `0x0c35`, and `0x007d` in the corresponding profile path | **SEMANTICALLY_MATCHING**; **PROVEN_BINARY** as table values |
| H209 FC03 address 209/count 15 | current routine around VMA `0x402447ac` writes FC `3`, address `0x00d1`, count `15`, CRC, and has the same response-validation shape | **SEMANTICALLY_MATCHING**; **PROVEN_BINARY** in current image |
| H180 FC03 | current dispatch supplies `180` to a dynamic read/response path around VMA `0x40245b07`; exact live count/order is not proven statically | **SEMANTICALLY_MATCHING**, count/order **UNKNOWN** |
| FC20 | exact live request prefix `01 20 00 00 00 64` is absent; no unambiguous 200-byte FC20 parser was isolated | **UNKNOWN**; retain earlier **PROVEN_LIVE** only |
| H188/FC06 | numeric `188` references occur in data/collector paths, but no unambiguous current FC06 write trigger/value path was isolated | **UNKNOWN** statically; earlier live FC06 observation remains **PROVEN_LIVE** |
| WiFi/cloud/inverter state areas | current strings name separate WiFi, cloud TCP, inverter, FOTA, local TCP, and serial receive tasks | **SEMANTICALLY_MATCHING**; cross-trigger policy **UNKNOWN** |

The current H43 and H209 routines are especially useful continuity anchors: the
field-by-field request construction, CRC placement, response CRC checks, and
post-response state handling remain recognizably the same despite relocation
and broader application changes. This does not recover a complete 3.1.0.5
poll schedule.

## FC20 and H188 revisit

The current image contains generic function/length dispatch and many `0x20`
and `0x64` constants, but no exact FC20 request prefix and no statically
isolated consumer that proves a 200-byte FC20 payload. The earlier live frame
pair remains the authoritative evidence for FC20 framing. Nothing here proves
that FC20 is a DDSU666 proxy; it should remain an opaque, CRC-checked
observation/cache object.

For H188, current code contains multiple immediate/address-like references to
188 in collector/network and data-management paths. They do not, by
themselves, establish a complete FC06 frame builder, trigger, value, retry
policy, or cloud-state relationship. The earlier one-frame live FC06 write is
therefore retained as live evidence, but not expanded into a binary-only
claim.

## Restoration feasibility

The result is **Case D / unresolved separation** rather than a safe Case A or
B conclusion:

* Stock code and per-device identity are not cleanly separated by the supplied
  evidence because the serial is absent from all searched forms and the tail
  records are not decoded.
* A length-preserving patch is not currently justified. No identity field,
  checksum, signature, duplicate-record rule, or cloud credential dependency
  has been proven.
* Stock firmware may use a serial, MAC/eFuse value, datalogger provisioning
  record, cloud token, or a combination. A printed serial alone may be
  insufficient.
* A stock restore on Shine B can only be expected to regenerate B's correct
  Growatt serial if a future code/data analysis proves a chip-ID/MAC-derived
  serial algorithm. This dump does not prove that. Do not clone Shine A's
  serial or tail identity into B.
* The duplicated tail sectors and the current nonblank secondary-firmware
  payload leave open the possibility that Shine B still retains original
  identity/configuration in untouched sectors. This can only be tested by
  comparing a complete read-only Shine B dump.

Never flash Shine A's image or identity onto Shine B. The desired end state is
that Shine A retains `XGD6CCN109` and Shine B is restored, if possible, using
only Shine B's own original identity and provisioning.

## Exact prerequisites before touching Shine B

1. Record Shine B's printed serial, model, hardware revision, flash size,
   crystal frequency, and current firmware provenance without changing it.
2. Obtain two complete, byte-identical 4 MiB read-only dumps from Shine B and
   record SHA-256 for both. Do not dump only the application range.
3. Record Shine B's own MAC and chip ID, then repeat the identity search using
   B's printed serial and B's own hardware identifiers in binary order,
   reversed order, ASCII, hexadecimal, decimal, and case variants. Do not
   search by or substitute Shine A's serial.
4. Compare Shine B against the supplied stock/current images by sector and
   inspect `0x3f8000–0x3fffff`, all possible OTA/config regions, and any
   unchanged payload blocks.
5. Determine whether Shine B's identity is in factory/SDK sectors, an
   untouched OTA/config partition, OTP/eFuse, or a cloud-provisioning record;
   identify record boundaries, redundancy, sequence fields, and checksum
   before considering any write.
6. If B's serial is not derived from its hardware ID, recover B's own complete
   provisioning/configuration records and any required cloud/device secret
   before a stock restore; keep credentials/tokens outside the repository.
7. Export/record any existing Shine B local configuration non-secretly and
   separately.
8. Define an offline recovery path and verify image/header checksums before any
  future flash operation. A future task must explicitly review the exact
   target offsets and recovery procedure first.

## Reusable tooling and validation

Added read-only, standard-library tools:

```text
tools/diff_shine_flash.py
tools/find_shine_identity.py
```

Examples:

```bash
python3 tools/find_shine_identity.py --json --mac e8:68:e7:74:f8:de \\
  --chip-id 0x0074f8de ShineWiFi-X-3.1.0.5.bin
python3 tools/diff_shine_flash.py --json stock.bin ShineWiFi-X-3.1.0.5.bin
```

Focused unit tests cover encoded/partial identity searches and sector/interval
diff output. Python compilation and `git diff --check` were also run. The
analysis JSON used for review remained in `/tmp` and is not a repository
deliverable.

## Open questions

* What exact format and checksum protect the duplicated SDK/config records?
* Is `ESP_74F8DE` an AP name derived at boot from an eFuse/MAC value, a default,
  or a stored configurable value?
* Is the Growatt serial supplied by an encoded provisioning record or cloud
  service outside the visible literal strings?
* Which partition contains the embedded secondary MCU/BMS payload, and is it
  updated independently?
* Does cloud provisioning bind a datalogger serial to a device secret outside
  the 4 MiB dump?
* Which parts of the 3.1.0.5 dynamic dispatch implement FC20 and the live H188
  event observed earlier?
