# The Nikobus protocol

Niko NV has never published a specification for the Nikobus bus, the
PC-Link serial interface, or the memory layout of the modules. This
document consolidates what the community has reconstructed and what
this library implements, so that the knowledge survives independently
of any one install or tool. The source modules referenced below are
the authority whenever the two disagree.

**Ground rules**

- Every item here is implemented or directly used by this library.
  Items marked ‡ are documented formats that this library only reads
  partially or not yet — treat them as the current best understanding.
- Nikobus is a trademark of Niko NV. This project is not affiliated
  with, endorsed by, or supported by Niko NV.
- Corrections are welcome — the most valuable contribution is a serial
  trace from an install that contradicts something below.

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
this library repeats simulated presses.

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
| `0x15` / `0x16` | `15 lo hi s1..s6 FF` / `16 lo hi s7..s12 FF` | short ack | Set outputs 1–6 / 7–12 — **all six channels of a group atomically**; roller values are masked to 2 bits |
| `0x10` | `10 lo hi blk_lo blk_hi` | `lo hi` + 16 bytes | Read 16-byte memory block `blk` (offset = `blk × 16`) |
| `0x22` | `22 lo hi blk_lo blk_hi` | `lo hi` + 8 bytes | Read 8-byte memory block (dimmer-class modules) |
| `0x13` | `13 lo hi 00` | 7 bytes: `FF lo hi ? ? crc_lo crc_hi` | The CRC16 (same algorithm as §3) the module computes over its whole memory image |
| `0x1D` | `1D lo hi` | 9 bytes: `FF lo hi YY MM DD hh mm ss` | **PC-Link date/time**, `YY` = year − 2000 |
| `0x1E` | `1E lo hi YY MM DD hh mm ss FF` | short ack | Set the PC-Link date/time |
| `0x18` / `0x19` ‡ | `18 lo hi` / `19 lo hi` | short ack | Programming ("link") mode on / off |
| `0x14` / `0x21` ‡ | `14|21 lo hi blk_lo blk_hi <block bytes>` | short ack | Write a 16-byte / 8-byte memory block |
| `0x23` ‡ | `23 lo hi` | short ack (slow, ~20 s) | Clear the module EEPROM |
| `0x1B` / `0x1C` ‡ | `1B lo hi` / `1C lo hi` | short ack | Mark the memory image valid / invalid |

`0x1A`, `0x1F` and `0x20` exist in the function-code space but never
produce an accepted reply. The write-side commands (‡) are documented
for completeness; this library does not write module memory.

### Timing and retries

Observed / implemented values (`const.py`):

| What | Value |
|---|---|
| Inter-command delay on the queue | 150 ms |
| Ack / answer wait | up to 15 s per attempt, 3 attempts |
| Register-read ACK latency (real hardware) | 300–700 ms |
| Simulated press repeat | 3 × 50 ms ("2 to register, 3 to be sure") |

A robust sender uses staged retries (a first immediate send, then
1–3 s back-offs) and polls the reply every ~50 ms; a module that is
not answering `0x11` is treated as absent.

---

## 5. Button telegram addressing

A `#N` telegram address encodes the transmitter's 24-bit physical
address **bit-reversed**, with the pressed key folded in
(`protocol.py: nikobus_to_button_address`):

```
combined = (key_code << 21) | (physical_address >> 2)   # 24 bits
wire     = bit_reverse_24(combined)
```

| Key | Code | Key | Code |
|---|---|---|---|
| 1A | `0b101` | 2A | `0b100` |
| 1B | `0b111` | 2B | `0b110` |
| 1C | `0b001` | 2C | `0b000` |
| 1D | `0b011` | 2D | `0b010` |

For a given transmitter the **B key's bus address is the A key's with
the first hex nibble incremented by 4**.

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

### PC-Link registry and register bands

Controller memory is read in 16-byte blocks (§4, `0x10`); the "sub-byte"
of this library's scan plans is simply the high byte of the block index
(`block = sub << 8 | register`, offset = `block × 16`). The bands read
by `discovery.py` (`_MODULE_SCAN_PROFILES`) map to controller memory as:

| Controller | Band | Memory offset |
|---|---|---|
| PC-Logic | sub `00` reg `06..3F` | 0x60.. (logic tables) |
| PC-Logic | sub `00` reg `3E` | 0x3E0 (link band header) |
| PC-Logic | sub `02` reg `AF..EE` | 0x2AF0.. (link records, 6-byte entries) |
| PC-Logic | sub `03` reg `E8..F4` | 0x3E80.. (compressed input groups, 3-byte entries) |
| PC-Link | sub `00`/`01`/`04` bands | vendor header, secondary, status, module registry |

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
current firmware) and `count` the number of registry records that
follow — it bounds the sweep (`_registry_header_count`). The header also
carries the per-device **Component.Number** the Niko software shows as
"BP7"/"S1".

An all-`FF` record is an *empty slot*, not end-of-data; only the
`$18FFFF…` trailer, the header count, or a run of consecutive empties
ends a sweep. Inside the link bands, addresses are big-endian 3-byte
values; compressed-group entries additionally carry a 24-bit
bit-reversed address (‡, `pc_logic_decoder.py`).

---

## 7. Output-module memory images

Switch (05-000-02, 05-002-02), roller (05-001-02) and dimmer (05-007-02,
05-008-02) modules keep their button-link programming in an EEPROM
image that is read block by block (`api.py: read_module_memory`).
Erased memory reads `0xFF`.

### Switch / roller — 0x700 bytes, 16-byte blocks

| Offset | Content |
|---|---|
| `0x000–0x0FF` | **Hash index**: `img[h]` = index of the first link record whose button-address hash is `h` (`h` = byte-sum of the three address bytes), `0xFF` = none |
| `0x100–0x6F9` | **Link records, 6 bytes each**, indexed 0..254 (`0x100 + i × 6`) |
| `0x6FA` | Record count |

Record layout (as the module stores it; the bus returns the bytes of a
record in reverse order, which is what `switch_decoder.py` reads):

```
b0 = addr[23:16]
b1 = addr[15:8]
b2 = addr[7:2] | key[3:2]
b3 = param (high nibble) | mode (low nibble)
b4 = key[1:0] << 6 | addr[1:0] << 4 | channel
b5 = index of the next record with the same hash (chain)
```

`mode` is the link-mode number (M01…, `mapping.py: SWITCH_MODE_MAPPING`,
`ROLLER_MODE_MAPPING`), `param` the mode's timer/option index
(`SWITCH_TIMER_MAPPING`, `ROLLER_TIMER_MAPPING` — for roller modes this
is the **relay run time** the module applies to that link).

### Dimmer — 0xFD0 bytes, 8-byte blocks

| Offset | Content |
|---|---|
| `0x000–0x0FF` | Hash index (as above) |
| `0x100–0x7C7` | **Bank 0 link records, 8 bytes each** |
| `0x7C8` | Bank 0 record count |
| `0x7CA–0x7F9` | Per-channel configuration: 12 level bytes, 12 packed option bytes, 12 reserved ‡ |
| `0x900–0xFCF` | **Bank 1 link records** (same format) |

Record bytes `b0..b4` follow the switch layout; `b5` low nibble is the
**T2 ramp time** (`DIMMER_T2_RAMP`), `b6`/`b7` are reserved. Dimmers
answer 8-byte blocks (`0x22`), one record per block.

### Scan plans

The module-status reply (`0x11`) gives the record counts of bank A and
bank B; discovery reads exactly the blocks that hold them
(`discovery.py: _count_driven_passes`) — 6-byte records from block
`0x10` for switch/roller, 8-byte records from block `0x20` (bank 0) and
sub `01` block `0x20` (bank 1) for dimmers, plus the configuration
blocks `0xF8..0xFF`. A module that does not answer `0x11` is scanned with
the fixed vendor band (`_MODULE_SCAN_PROFILES`).

### Integrity

`0x13` returns the CRC16 (§3 algorithm) of the whole image; comparing it
with a CRC computed over a freshly read image verifies the programming
(`api.py: verify_module_memory`). `0x11`'s status bit reports an EEPROM
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
daylight-saving changes, so a host should resynchronise it. The
calendar area of the PC-Link image holds 21-byte schedule entries and
8-byte simulation entries ‡ (times are linearised to seconds since the
start of the week); this library does not decode them yet.

---

## 10. Known unknowns

- The exact bit packing of the feedback-module (05-207) LED records and
  of the PC-Link calendar entries.
- Link-record byte-0 target indices in the PC-Link registry are resolved
  against the in-scan registry buffer; more installs are needed to
  consider the mapping final (`pc_record_parser.py`).
- The low-bit packing of the logical-input formulas (§8) is validated
  empirically; the class markers (`0x6`, `0x18`) may be refined.

If you can capture traffic that settles any of these, please open an
issue with the raw trace.
