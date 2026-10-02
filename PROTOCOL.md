# The Nikobus protocol

Niko NV has never published a specification for the Nikobus bus, the
PC-Link serial interface, or the memory layout of the modules. This
document consolidates what the community has reconstructed, what the
vendor's own software was found to do, and what this library
implements, so that the knowledge survives independently of any one
install or tool. The source modules referenced below are the authority
whenever the two disagree.

**Ground rules**

- Items marked ‡ are documented formats that this library does not
  implement, or reads only partially. The write side of the protocol
  (§4) is ‡ by decision, not by lack of knowledge: this library reads
  state and presses keys, and nothing in it writes to a module or
  enters link mode.
- Nikobus is a trademark of Niko NV. This project is not affiliated
  with, endorsed by, or supported by Niko NV.
- Corrections are welcome — the most valuable contribution is a serial
  trace from an install that contradicts something below.

**Sources.** Serial traces from real installs (the Nikobus-HA issue
tracker), and, since September 2026, the vendor's Nikobus PC software
4.3.1 decompiled: `nikobus.exe`, `serial.dll`, the product plugins
`Niko_05_000_01` (switch and roller), `Niko_05_007` (dimmer),
`Niko_05_010` (colour controller), `Niko_05_100` (PC-Link),
`Niko_05_200` (PC-Logic), `Niko_05_202` (audio), `Niko_05_207` /
`Niko_05_207a` (feedback module), and its `product.mdb` (database
version 21008). The block-by-block
maps of what each plugin writes are in the Nikobus-HA repository,
`documentation/vendor-plugins-memory-maps.md` and
`documentation/rgb-controller-memory-map.md`, next to the binaries;
this document keeps what a reader of the bus needs.

---

## 1. Transport

The bus is reached through a **PC-Link module (05-200)** exposing an
RS-232 port, in practice through a USB-serial adapter or a
serial-over-TCP bridge.

- **9600 baud, 8N1** (`connection.py`). All traffic is ASCII; frames end
  with `\r`.

### Handshake

On connect the PC-Link is initialised with (`const.py`):

```
++++  ATH0  ATZ  $10110000B8CF9D  #L0  #E0  #L0  #E1
```

The modem-style prefix resets the interface. `$10110000B8CF9D` is
function `0x11` addressed to `0000` — a PC-Link presence check — whose
acknowledgement is `$0511`. `#L0`/`#E0`/`#E1` control link/echo mode;
after `#E1` the PC-Link relays bus events to the serial port.

---

## 2. Frame families

| Prefix | Direction | Meaning |
|---|---|---|
| `#N<6 hex>` | both | Button-press telegram (physical or simulated). Real buttons repeat it while held. |
| `#A` | to bus | Broadcast address inquiry; controllers answer with a `$18…` status frame. |
| `#L<n>` / `#E<n>` | to PC-Link | Link / echo mode (`#L0\r#E1\r` enables event relay, `#L0\r#E0\r` disables it). |
| `$05<func>` | from PC-Link | **Acknowledgement of command `<func>`** — for every function code (`$0511`, `$0512`, `$0515`, `$0516`, `$0517`, `$051D`, …). |
| `$0EFF…` | from bus | Short answer to a set-type command (`FF` + status + CRC). |
| `$18…` | from bus | 7-byte answers: module status, EEPROM CRC, and the `$18FFFF…` end-of-data trailer. |
| `$1C…` | from bus | 9-byte answers: output states (replies to `0x12`/`0x17`) and the PC-Link clock. |
| `$2E…` / `$1E…` | from bus | 16-byte / 8-byte memory block answers. |
| `$14…` / `$10…` / `$12…` / `$1E…` | to bus | Framed commands (§3); the prefix is the frame length, not the function. |

Simulated presses are sent as `#N<addr>\r#E1` (`api.py`). Modules only act
on a press telegram seen **at least twice** (the bus's noise guard), so
this library repeats simulated presses. The vendor software does the
same: its simulation mode has no direct command for a dimmer or colour
output and presses the linked key on the bus, held by a timer.

---

## 3. Framing and checksums

A command is a binary payload wrapped in ASCII (`protocol.py:
make_pc_link_command`):

```
payload  = <func:1> <module addr, little-endian:2> [<args…>]
frame    = "$" <10 + len(payload_hex) : 2 hex> <payload_hex> <CRC16:4 hex> <CRC8:2 hex>
```

- **CRC16 — CRC-16/CCITT** (poly `0x1021`, init `0xFFFF`) over the
  binary payload bytes, big-endian.
- **CRC8** (poly `0x99`, init `0x00`) over the ASCII characters of the
  frame built so far (including `$` and the length).

Both are confirmed against the vendor software: `nikobus.exe` computes
the CRC16 over the binary payload, hands the frame to `serial.dll`,
and the DLL adds the `$`, the length byte, the hex encoding, the CRC8
(its checksum type 2, polynomial `0x99`) and the carriage return.

Replies use the same wrapping: the length byte is `10 + <hex length of
the data>`, the data starts at character 3, and CRC16 + CRC8 follow it
(`protocol.py: reply_payload`). Hence a 7-byte answer is `$18…`, a 9-byte
answer `$1C…`, a 2 + 16 byte block answer `$2E…`, a 2 + 8 byte block
answer `$1E…`.

**Module addresses are little-endian on the wire**: module `0x86F5`
appears as `F586`. Storage in this library uses the big-endian form
(`86F5`); the wire form is derived (`protocol.py: wire_address`).

### Acknowledgement and answer matching

For every command the PC-Link first answers `$05` + function code
(`command.py: _prepare_ack_and_answer_signals`). The data answer, when
there is one, echoes the module address — either first or behind a
leading `FF` byte:

| Function | Answer frame |
|---|---|
| `0x11` module status | `$18` + addr |
| `0x13` EEPROM CRC | `$18FF` + addr |
| `0x12` / `0x17` get outputs | `$1C` + addr |
| `0x1D` clock | `$1CFF` + addr |
| `0x10` / `0x22` block read | `$2E` / `$1E` + addr |
| set-type commands | `$0EFF` + addr |

---

## 4. Command table

| Func | Payload (before CRC16) | Reply data | Meaning |
|---|---|---|---|
| `0x11` | `11 00 00` | ack only | PC-Link presence check (handshake) |
| `0x11` | `11 lo hi` | 7 bytes: `lo hi status type ? countA countB` | **Module status**: `status & 1` = EEPROM error; `type` signature (`0x50` PC-Link, `0x40` PC-Logic); `countA`/`countB` = records in the module's link tables |
| `0x12` / `0x17` | `12 lo hi` / `17 lo hi` | 9 bytes: `lo hi s1..s6 ?` | Output states of channels 1–6 / 7–12 |
| `0x15` / `0x16` | `15 lo hi s1..s6 FF` / `16 lo hi s7..s12 FF` | short ack | Set outputs 1–6 / 7–12 — **all six channels of a group atomically** (a roller channel takes `0x00` stop, `0x01` open, `0x02` close) |
| `0x10` | `10 lo hi blk_lo blk_hi` | `lo hi` + 16 bytes | Read 16-byte memory block `blk` (offset = `blk × 16`) |
| `0x22` | `22 lo hi blk_lo blk_hi` | `lo hi` + 8 bytes | Read 8-byte memory block (dimmer-class modules) |
| `0x13` | `13 lo hi 00` | 7 bytes: `FF lo hi ? ? crc_lo crc_hi` | The CRC16 (same algorithm as §3) the module computes over its memory image — the whole image for switch and roller modules, both banks but not `0x7FA..0x7FF` for dimmers (§7, Integrity) |
| `0x1D` | `1D lo hi` | 9 bytes: `FF lo hi YY MM DD hh mm ss` | **PC-Link date/time**, `YY` = year − 2000 |
| `0x1E` | `1E lo hi YY MM DD hh mm ss FF` | short ack | Set the PC-Link date/time |
| `0x18` / `0x19` ‡ | `18 lo hi` / `19 lo hi` | short ack | Link (programming) mode on / off |
| `0x14` / `0x21` ‡ | `14|21 lo hi blk_lo blk_hi <block bytes>` | short ack | Write a 16-byte / 8-byte memory block |
| `0x23` ‡ | `23 lo hi` | short ack (slow, ~20 s) | Clear the module EEPROM |
| `0x1B` / `0x1C` ‡ | `1B lo hi` / `1C lo hi` | short ack | Mark the memory image valid / invalid (PC-Logic and PC-Link only) |

`0x1A`, `0x1F` and `0x20` exist in the function-code space but never
produce an accepted reply.

**How the vendor programs a module** (‡, from `nikobus.exe`; recorded
so a capture of the vendor software can be read, and for nothing else):
link mode on, clear, every non-empty 16-byte block written with `0x14`
(block index = byte address ÷ 16; dimmers take 8-byte blocks with
`0x21`), link mode on again, the module's `0x13` CRC compared with the
CRC16 of the image, link mode off. PC-Logic and PC-Link are bracketed
with `0x1C` (invalid) before and `0x1B` (valid) after instead, and
their CRC is not checked; the audio module's CRC is not checked either;
the feedback module and the two colour products stay in link mode for
the whole write, where the other families are taken out and back in.
The vendor never reads those three families back: its upload routine
skips memory classes 10, 11 and 12 before sending a frame (§11).

### Timing and retries

Implemented values (`const.py`):

| What | Value |
|---|---|
| Inter-command delay on the queue | 150 ms |
| A command's total wait | 15 s: 3 attempts of 5 s each (the ack within 5 s, the answer within 1.5 s of the ack) |
| Register read during a scan | 1.5 s for the ack, 0.5 s for the block, one retry |
| Module status (`0x11`) during discovery | 20 s |
| Register-read ACK latency (real hardware) | 300–700 ms |
| Simulated press repeat | 3 × 50 ms ("2 to register, 3 to be sure") |

The vendor software (`nikobus.exe`, not `serial.dll`) sends a frame up
to five times per attempt, waits 50 ms × 5 × a per-call factor with a
floor of one second, and makes up to the caller's number of attempts
with a growing pause between them. A module that is not answering
`0x11` is treated as absent by both.

---

## 5. Button telegram addressing

A `#N` telegram address is the plate's 24-bit address shifted left by
two with the pressed key's 3-bit code in the low bits, the whole
**bit-reversed** (`protocol.py: nikobus_to_button_address`,
`nkb.parser.per_key_bus_address`):

```
record = (plate_address << 2) | key_code     # 24 bits, the form link records store (§7)
wire   = bit_reverse_24(record)              # the #N address
```

This is the vendor software's own rule: its simulation mode presses a
key exactly this way, and a validating install confirms it — plate
`124A36`, key 1C (code 0), presses `#N1B1492`; key 1D (code 2),
`#N5B1492`. In the wire address the code lands in the first hex digit,
bit-reversed: code 1 adds 8, code 2 adds 4, code 4 adds 2.

The code a key label carries depends on how many keys the plate has
(`mapping.py: KEY_MAPPING`, from the nibble each key adds):

| Plate | 1A | 1B | 1C | 1D | 2A | 2B | 2C | 2D |
|---|---|---|---|---|---|---|---|---|
| 4 keys | 1 | 3 | 0 | 2 | | | | |
| 8 keys | 5 | 7 | 4 | 6 | 1 | 3 | 0 | 2 |
| 2 keys | 1 | 3 | | | | | | |

So on a four-key plate the **B key's bus address is the A key's with
the first hex nibble incremented by 4**, and the D key's is the C key's
plus 4. An earlier revision of this section gave a formula that put the
code in the top bits before reversal, with a fixed code table; that was
wrong, and so were the two helpers that implemented it until 0.45.0.

`convert_nikobus_address` maps a stored 24-bit address to the bus form
by reversing 21 bits and **adding** the 3-bit key field into the low
bits; because it adds rather than ORs it is not a bijection, so there is
no closed-form inverse (see the note in `discovery/protocol.py`).

---

## 6. Controllers

Controllers answer the broadcast `#A` with the same 7-byte layout as
the module-status reply: `$18 <addr> 00 <sig> 0F 3F FF <crc>`, `sig`
`0x50` for a PC-Link (05-200) and `0x40` for a PC-Logic (05-201)
(`const.py: PC_LINK_INVENTORY_SIGNATURE_BYTE`). On installs with both,
the signature is what keeps inventory reads aimed at the right device.

Controller memory is read in 16-byte blocks (§4, `0x10`); the "sub-byte"
of this library's scan plans is simply the high byte of the block index
(`block = sub << 8 | register`, offset = `block × 16`).

### PC-Link image, as the vendor writes it

| Byte address | Length | Content | Library plan (`_MODULE_SCAN_PROFILES`) |
|---|---|---|---|
| 90 | 46 | Header; byte `0x28` a mode flag (`0x78` / `0xF0` / `0`) ‡ | sub `00` `0x05`–`0x09` |
| 150 | 4 | | sub `00` `0x09` |
| 995 | 2 + count × 8 | Presence simulation: on and off events per address, minutes × 12 ‡ | sub `00` `0x3E` (first block only) |
| 5900 | 561 | The 100 calendar channels, count at +`0x230` | sub `01` `0x70`–`0x93` |
| 6496 | 3 | | sub `01` `0x96` |
| 6498 | 2 + count × 21 | Calendar appointments, 21 bytes each ‡ | sub `01` `0x96` (first block only) |
| 18000 | 65 | Status band | sub `04` `0x65`–`0x69` |
| 19000 | up to 13268 | **Registry**: header `5E 55 AA AA` + count, then one 16-byte record per component, ordered by location and component | sub `04` `0xA3`–`0xD3`, bounded by the header count |

The registry is the one block the vendor plugin never reads back
(`GetDLLReadInfo` returns a length of zero for it); this library reads
it because the module answers, and its header recognition matches how
the vendor writes it.

### PC-Logic image, as the vendor writes it

| Byte address | Length | Content | Library plan |
|---|---|---|---|
| 99 | 385 | Logic programme | sub `00` `0x06`–`0x3F` |
| 998 | 2 + count × 6 | **Input table**: the links whose output is the PC-Logic, `[addr 3] [input] [slot] [mode]` each, the address in the record form of §5 (‡: bytes 3–5 read from the plugin's composer, no capture yet) | sub `00` `0x3E`, then to the last record (`PcLogicDecoder.extension_passes`); decoded and reported, not merged |
| 11000 | 640 | Link records, 6-byte entries | sub `02` `0xAF`–`0xEE` |
| 12000 | 2 + count × 5 | Up to 64 physical output addresses ‡ | sub `02` `0xEE` (first block only) |
| 16000 | 192 | Compressed input groups, 3-byte entries | sub `03` `0xE8`–`0xF4` |

### Registry and link records

Every read returns one **16-byte record**. Two record shapes occur in
the registry area (`discovery/pc_record_parser.py`):

```
registry: <marker> 00 00 00 <type> 00 00 00 <addr_lo> <addr_hi> 00 00 <slot> 00 00 00
link:     <chan>   00 00 00 <mode> 00 00 <flag> <p0> <p1> <p2> 00 <slot> 00 00 00
```

Bytes 1–3 are always `00 00 00` — the cleanest filter against the
byte-ramp filler pages (`00 01 02 03 …`) the PC-Link emits at low
register indexes. The **registry header** ends with `<ver> 55 AA AA
<count:u32 LE>`: `ver` is a header version in `0x49..0x5E` (`0x5E` on
current firmware, and the value the vendor plugin writes) and `count`
the number of registry records that follow — it bounds the sweep
(`_registry_header_count`). The header also carries the per-device
**Component.Number** the Niko software shows as "BP7"/"S1".

An all-`FF` record is an *empty slot*, not end-of-data; only the
`$18FFFF…` trailer, the header count, or a run of consecutive empties
ends a sweep. Inside the link bands, addresses are big-endian 3-byte
values; compressed-group entries additionally carry a 24-bit
bit-reversed address (‡, `pc_logic_decoder.py`).

---

## 7. Output-module memory images

Switch (05-000-02, 05-002-02), roller (05-001-02), dimmer (05-007-02,
05-008-02) and audio (05-205) modules keep their button-link
programming in an EEPROM image that is read block by block
(`api.py: read_module_memory`). Erased memory reads `0xFF`. The
layouts below were reconstructed from dumps and then checked against
the vendor plugin that writes each one; they agree.

### Link modes are stored as the vendor's mode index

The mode byte of every record family holds `product.mdb`'s
`LinkModeBase.LinkIDNumber`, not the M number printed in the
software. For the switch family: M01–M08 are 0–7, M11 8, M12 9, M13
10, M14 11, M15 12. The roller module numbers its modes the same way
(M01 0, M03 2, M04 3, M05 4, M06 5, M07 6 in the database), the
dimmer M01–M08 as 0–7 with M11 8, M12 9, M13 10, M14 11, and the
colour controller continues M16 13 … M21 18. `mapping.py`
(`SWITCH_MODE_MAPPING`, `ROLLER_MODE_MAPPING`, `DIMMER_MODE_MAPPING`)
carries these tables; the switch table's M13–M15 entries were
corrected in 0.43.0 from the plugin.

M13 on a switch module is the **sequencer**: in the database it is a
link to the module's sequencer object rather than to an output
channel, and the plugin's own upload decoder flags a record as a
sequencer exactly when its mode nibble is 10. What the channel nibble
of such a record designates has not been seen on a real module (§10).

### Switch / roller — 0x700 bytes, 16-byte blocks

| Offset | Content |
|---|---|
| `0x000–0x0FF` | **Hash index**: `img[h]` = index of the first link record whose button-address hash is `h` (`h` = byte-sum of the three address bytes, which is how the plugin computes it), `0xFF` = none |
| `0x100–0x6F9` | **Link records, 6 bytes each**, indexed 0..254 (`0x100 + i × 6`) |
| `0x6FA` | Record count |

Record layout as the plugin composes it (the bus returns the bytes of a
record in reverse order, which is what `switch_decoder.py` reads):

```
b0 = addr[23:16]                 addr = plate << 2 | key_code (§5)
b1 = addr[15:8]
b2 = addr[7:2] << 2 | p4[3:2]
b3 = T1 << 4 | mode              mode = LinkIDNumber
b4 = p4[1:0] << 6 | addr[1:0] << 4 | channel
b5 = index of the next record with the same hash (chain)
```

`p4` is the link's fourth parameter, which selects the key half; the
library reads the high nibble of `b4` as its key index. `T1` is the
mode's timer/option index (`SWITCH_TIMER_MAPPING`,
`ROLLER_TIMER_MAPPING` — for roller modes this is the **relay run
time** the module applies to that link).

### Dimmer — 0xFD0 bytes, 8-byte blocks

| Offset | Content |
|---|---|
| `0x000–0x0FF` | Hash index (as above) |
| `0x100–0x7C7` | **Bank 0 link records, 8 bytes each** |
| `0x7C8` | Bank 0 record count |
| `0x7CA–0x7F9` | Per-channel configuration, 48 bytes the vendor writes as a fixed block: 12 level bytes, 12 bytes of two nibbles each, 24 more ‡ |
| `0x900–0xFCF` | **Bank 1 link records** (same format) |

Record bytes `b0..b4` follow the switch layout (`p3` in place of
`p4`); `b5` low nibble is the **T2 ramp time** (`DIMMER_T2_RAMP`),
`b6`/`b7` are reserved. Dimmers answer 8-byte blocks (`0x22`), one
record per block. The configuration block holds no links and is not
read by discovery: run through the link decoder it yields phantom
buttons (0.40.1).

### Audio (05-205) — 16-byte blocks

| Byte address | Content |
|---|---|
| 100 | Settings, 241 bytes; byte 240 a flag ‡ |
| 998 | Link count |
| 1000 | Index table, count × 2 bytes, entry n = n ‡ |
| 4998 | Link count again — sub `01` register `0x38`, offset 6, the `<count> 00` header `audio_decoder.py` looks for |
| 5000 | **Link records, 6 bytes each**, up to 1864 of them |

Record: `[addr 23:16] [addr 15:8] [addr 7:0] [function] [object & 0xF]
[01]`; the plugin's upload decoder takes a record only when its sixth
byte is 1. This library reads the fixed band first (sub `01`
`0x38`–`0x5F`, about 105 records), takes the count from its head and
reads on to the last record (`AudioDecoder.extension_passes`), across
sub-bytes `01` to `03` as the table requires.

### Feedback module (05-207) ‡

An image of `0x7900` bytes in five blocks: LED records (8 bytes each,
count-driven, from byte 0 up to `0x4000`), then fixed blocks at
`0x4000` (`0x2000` bytes), `0x6000` and `0x6100` (`0x100` each) and
`0x6200` (`0x1700`). The module answers no block read outside link
mode, and this library does not read it (0.37.0). The layout is
recorded for reading a capture of the vendor software.

### Colour controller (340-00112, 340-00111, 340-00113) ‡

An image of 7968 bytes in four blocks: an LED calibration profile at
byte 0 (400 bytes), the **link table at `0x190`, 128 slots of 18
bytes**, the colour paths at `0xA90` (512 points of 10 bytes and 32
descriptors of 4 bytes) and a 16-byte settings record at `0x1F10`.
An 18-byte record holds the button address (bytes 0–2, the form of
§5), `LinkIDNumber << 3 | channel` (byte 3), two 16-bit timer
parameters, a 32-bit CIE xy colour (`50 0D 54 3A` is D65 white), a
level, a seventh parameter and the colour-path index; an empty slot is
eighteen `0xFF`. The vendor writes the table and never reads it back;
the controller answers nothing but the output-state query outside link
mode, and this library reads its state only (`rgb.py`:
`[on] [R] [G] [B] 00 00`, each a lit-or-not flag). Field by field:
`documentation/rgb-controller-memory-map.md` in the Nikobus-HA
repository.

### Scan plans

The module-status reply (`0x11`) gives the record counts of bank A and
bank B; discovery reads exactly the blocks that hold them
(`discovery.py: _count_driven_passes`) — 6-byte records from block
`0x10` for switch/roller, 8-byte records from block `0x20` (bank 0) and
sub `01` block `0x20` (bank 1) for dimmers. This is also how the vendor
reads a module back: its plugins report each block's address, length
and block size, and the software multiplies the status count by the
record length. A module that does not answer `0x11` is scanned with
the fixed vendor band (`_MODULE_SCAN_PROFILES`).

### Integrity

`0x13` returns the CRC16 (§3 algorithm) of the image — the whole image
for switch and roller modules; for dimmers both banks with the six
bytes `0x7FA..0x7FF` (a version/flags word) left out, the only coverage
that reproduces a real 05-007's reported CRC (`api.py:
MODULE_CRC_RANGES`); comparing it
with a CRC computed over a freshly read image verifies the programming
(`api.py: verify_module_memory`). This is the same check the vendor
software runs after a write. `0x11`'s status bit reports an EEPROM
error the module detected itself.

---

## 8. Logical-input address schemes (05-201 / 05-206)

Controllers with logical inputs compute the bus addresses of those
inputs from their **own module address and a slot index**
(`discovery/protocol.py: derive_pc_logic_input_physicals`):

**PC-Logic (05-201)** — validated on three independent installs:

```
input_physical = 0x600000 | ((module_addr >> 1) << 4) | slot     # slot = 1..6
```

**Modular Interface (05-206)** — validated on two installs:

```
input_physical = 0x180000 + module_addr + slot                   # slot = 1..N
```

The input's **A bus address** is `convert_nikobus_address(input_physical)`
and the **B bus address** is the A address with the first nibble `+4`
(§5). Example, PC-Logic `0x940C` slot 1: physical `0x64A061` → A `21814B`,
B `61814B`. The PC-Logic's own link records carry these inputs as
24-bit addresses with high byte `0x60`, consistent with the formula.

Through library 0.33.x the 05-201 formula was applied to both families,
so 05-206 inputs were synthesized on addresses the hardware never emits
(Nikobus-HA issue #485); fixed in 0.34.0.

---

## 9. PC-Link clock and calendar

The PC-Link keeps a real-time clock (`0x1D`/`0x1E`, §4) that drives its
calendar and presence-simulation functions; it does not know about
daylight-saving changes, so a host should resynchronise it, as the
vendor software does from the PC's clock. The calendar area of the
image (§6) holds the 100 calendar channels at byte 5900, the
appointments as 21-byte entries from 6498 and the presence-simulation
events as 8-byte entries from 995 (on and off per address, in minutes
× 12) ‡; this library does not decode them. A host fires a calendar
channel by pressing it, like any key.

---

## 10. Known unknowns

- What the channel nibble of a sequencer (M13) record on a switch
  module designates; no such record has been captured.
- The meaning of the dimmer's 48-byte per-channel configuration block,
  of the PC-Link header flag at byte `0x28`, and of the audio module's
  settings block.
- The bit packing of the feedback-module (05-207) LED records and of
  the PC-Link calendar and presence-simulation entries.
- Whether the colour controller and the feedback module answer block
  reads inside link mode. Untested, and not a host's business: entering
  link mode is a programming command.
- Link-record byte-0 target indices in the PC-Link registry are resolved
  against the in-scan registry buffer; more installs are needed to
  consider the mapping final (`pc_record_parser.py`).
- The low-bit packing of the logical-input formulas (§8) is validated
  empirically; the class markers (`0x6`, `0x18`) may be refined.

If you can capture traffic that settles any of these, please open an
issue with the raw trace.

---

## 11. Memory classes and the vendor's plugins

The vendor software dispatches on `product.mdb`'s `EEPROMtype`, its
"memory class", and loads one plugin per class to build the image:

| Class | Product | Plugin | Read back by the vendor |
|---|---|---|---|
| 1 | Switch module 05-000-02 | `Niko_05_000_01` | yes, count-driven |
| 9 | Compact switch 05-002-02 | `Niko_05_000_01` | yes |
| 2 | Roller module 05-001-02 | `Niko_05_000_01` | yes |
| 3 | Dimmer 05-007-02 | `Niko_05_007` | yes |
| 8 | Compact dimmer 05-008-02 | `Niko_05_007` | yes |
| 4 | PC-Logic 05-201, SMS module 05-203 | `Niko_05_200`, `Niko_05_201a` | fixed blocks |
| 5 | PC-Link 05-200 | `Niko_05_100` | fixed blocks, not the registry |
| 7 | Audio 05-205 | `Niko_05_202` | yes, count-driven |
| 10 | Feedback module 05-207 | `Niko_05_207` (+ `207a`, report only) | never |
| 11 | RGB plinth light 340-00111 | `Niko_05_010` | never |
| 12 | Colour controller 340-00112 (colour and mono profiles) | `Niko_05_010` | never |

Wall buttons and other transmitters have no memory class; they hold no
image.

### The device-type byte is the product's database key

The type byte a component is filed under in the PC-Link registry (§6,
`DEVICE_TYPES` in `mapping.py`) is `product.mdb`'s `KeyProductBase`,
the primary key of `ProductBase`. Checked over the whole catalogue:
28 of the 33 catalogued bytes name the product row with that key (the
five others are the library's own aliases — two virtual products, and
three buttons the database lists under an alternative code). An
earlier revision of this document said the database carried no
device-type byte; it does, as its key. So every product Niko ever
listed has a known byte, whether or not a module of it has been seen:

| Byte | Key | Product | Vendor ref | Address | Status |
|---|---|---|---|---|---|
| `0x45` | 69 | RGB plinth light 340-00111 | `S_DB_DIM_PLINT` | 16-bit | catalogued, routed as `rgb_module` (first seen 2026-09-30; state reply not yet captured) |
| `0x47` | 71 | Colour controller 340-00112, mono profile | `S_DB_DIM_MONOCTRL` | 16-bit | catalogued, routed as `rgb_module`; not yet observed |
| `0x48` | 72 | Outdoor sensor 430-00502 | `S_DB_BUITEN_SENSOR` | 22-bit | not catalogued |
| `0x49` | 73 | Smoke detector 420-00005 | `S_DB_ROOKMELDER` | 22-bit | not catalogued |
| `0x2E` | 46 | SMS module 05-203 (output) | `S_DB_SMSOUT` | 16-bit | not catalogued |
| `0x24` | 36 | RF plate, 16 keys 05-310 | `S_DB_KNOP_16_RF868` | 22-bit | not catalogued |
| `0x36` | 54 | RF plate, 8 keys 05-305 (410-00003) | `S_DB_RF_WAND_8` | 22-bit | not catalogued |
| `0x38`, `0x3C` | 56, 60 | RF box 05-315, 2 and 4 channels | `S_DB_RFBOX2`, `S_DB_RFBOX4` | 22-bit | not catalogued |
| `0x3E` | 62 | Remote 05-313 | `S_DB_REMOTE5x1CH` | 22-bit | not catalogued |
| `0x27` | 39 | Remote 05-081 | `S_DB_REMOTE_CONTROL_2` | 22-bit | not catalogued |
| `0x1A` | 26 | IR plate 05-09x, 4 keys | `S_DB_KNOP_4_IR_UNIQUE` | 22-bit | not catalogued |
| `0x29` | 41 | Modular interface 05-055 | `S_DB_MODUL_INTERF` | 22-bit | not catalogued |
| `0x20` | 32 | PIR 05-045 | `S_DB_PIR_OLD` | 22-bit | not catalogued |
| `0x2C`, `0x2D` | 44, 45 | Audio inputs on a 4- / 8-key plate | `S_DB_AUDIO_IN4`, `S_DB_AUDIO_IN8` | 22-bit | not catalogued |
| `0x0D` | 13 | PC-Logic slave 05-201 | `S_DB_LOGIC_SLAVE` | 16-bit | not catalogued |

The uncatalogued ones wait for a real install: the byte is known, but
a button-class entry needs its channel count and a module-class entry
needs its state reply before the host can build anything from it. The
`type` signature of a module's `0x11` reply is a different byte and is
still learned by observing one.
