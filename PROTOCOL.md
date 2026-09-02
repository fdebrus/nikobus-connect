# The Nikobus protocol, as far as we know it

Niko NV has never published a specification for the Nikobus bus
protocol, the PC-Link serial interface, or the register layout of its
modules. Everything in this document is the result of reverse
engineering: serial traces of the official PC software, captures from
real installations, and controlled experiments on live hardware.
It documents what this library implements — the source modules
referenced below are the authority whenever the two disagree.

**Ground rules for this document**

- A claim only appears here if it has been observed on real hardware.
  Where the evidence is thin (a single install), that is said
  explicitly.
- Nikobus is a trademark of Niko NV. This project is not affiliated
  with, endorsed by, or supported by Niko NV.
- Corrections and new captures are welcome — the most valuable
  contribution is a serial trace from an install that contradicts
  something below.

---

## 1. Transport

The bus is reached through a **PC-Link module (05-200)** exposing an
RS-232 port, in practice used through a USB-serial adapter (FTDI) or a
serial-over-TCP bridge.

- Serial parameters: **9600 baud, 8N1** (`connection.py`).
- All traffic is ASCII. Frames are terminated with `\r`.

### Handshake

On connect, the PC-Link is initialised with this sequence
(`const.py: COMMANDS_HANDSHAKE`):

```
++++  ATH0  ATZ  $10110000B8CF9D  #L0  #E0  #L0  #E1
```

The modem-style prefix (`++++`, `ATH0`, `ATZ`) resets the interface;
`$10110000B8CF9D` is a framed command (function `0x11`, see §3) whose
expected reply is **`$0511`**. `#L0`/`#E0`/`#E1` control the link/echo
mode; after `#E1` the PC-Link relays bus events to the serial port.

---

## 2. Frame families

Everything seen on the wire falls into a small set of prefixes:

| Prefix | Direction | Meaning |
|---|---|---|
| `#N<6 hex>` | both | A button-press telegram (physical or simulated). Real buttons repeat it for as long as they are held. |
| `#A` | to bus | Broadcast address inquiry — controllers answer with a `$18…` identity frame. |
| `#L<n>` / `#E<n>` | to PC-Link | Link/echo mode control (handshake only). |
| `$05<code>` | from PC-Link | Short status frames. `$0515` / `$0516` acknowledge a processed set-state command; `$0511` acknowledges the handshake init. |
| `$10…` | to bus | Framed PC-Link command (see §3): get output state (`$1012`/`$1017`), plus the handshake init (`$1011`). |
| `$1C…` | from bus | Feedback-module answer frames (channel state payloads). |
| `$14…` | to bus | Inventory / register-read commands (see §5). |
| `$2E…`, `$1E…` | from bus | Register-read data answers. |
| `$18…` | from bus | Controller-originated frames: the `#A` identity answer, and the `$18FFFF…` end-of-data trailer that short-circuits a register sweep. |

Simulated button presses are sent as `#N<addr>\r#E1` (`api.py`).
Modules only act on a press telegram seen **at least twice** (the bus's
noise/collision guard), which is why this library repeats simulated
presses — a single frame is unreliable under bus contention.

---

## 3. Command framing and checksums

PC-Link commands are built by `protocol.py: make_pc_link_command`:

```
payload  = <func:1 byte> <module addr, little-endian:2 bytes> [<args…>]
frame    = "$" <len(payload_hex)+10 : 2 hex> <payload_hex> <CRC1:4 hex> <CRC2:2 hex>
```

Two checksums stack:

- **CRC1 — CRC-16/CCITT** (poly `0x1021`, init `0xFFFF`), computed over
  the **binary payload bytes**, appended as 4 uppercase hex digits.
- **CRC2 — CRC-8** (poly `0x99`, init `0x00`), computed over the
  **ASCII characters** of the frame built so far (including the `$` and
  length), appended as 2 hex digits.

Note the mixed levels: CRC1 protects the bytes, CRC2 protects the ASCII
rendering. Both are required; the reference implementations are
`calc_crc1` / `calc_crc2` in `protocol.py`.

**Module addresses on the wire are little-endian**: module `0x86F5`
appears in a frame as `F586`. The convention throughout this library is
to store the wire order (`F586`).

### Known function codes

| Func | Frame | Meaning |
|---|---|---|
| `0x11` | `$1011…` | Handshake init; answered `$0511`. |
| `0x12` | `$1012<addr>…` | Get output states, channels 1–6 (group 1). |
| `0x17` | `$1017<addr>…` | Get output states, channels 7–12 (group 2). |
| `0x15` | `$1015<addr><6 bytes>…` | Set output states, group 1. |
| `0x16` | `$1016<addr><6 bytes>…` | Set output states, group 2. |
| `0x10` | via `$14…` | Register read (see §5). |

Group number for a channel: `(channel + 5) // 6`. Setting states writes
**all six channels of a group atomically** — one frame moves a whole
half-module, which this library exploits for scene-style commits.

### Channel state bytes

| Value | Meaning |
|---|---|
| `0x00` | Off / stopped |
| `0xFF` | On (switch), full brightness (dimmer) |
| `0x01`–`0xFE` | Dimmer level |
| `0x01` | Roller: open (direction A) |
| `0x02` | Roller: close (direction B) |
| `0x03` | Roller: both outputs active — hardware motor-protection state; the module brakes. Observed on the bus when HA and the module disagree; clears itself for Nikobus-initiated moves, cleared by writing `0x00` for direct writes. |

---

## 4. Button telegram addressing

A `#N` telegram address encodes the transmitting device's 24-bit
physical address **bit-reversed**, with the pressed key folded in.
Encoding (`protocol.py: nikobus_to_button_address`):

```
combined = (key_code << 21) | (physical_address >> 2)   # 24 bits
wire     = bit_reverse_24(combined)
```

3-bit key codes:

| Key | Code | Key | Code |
|---|---|---|---|
| 1A | `0b101` | 2A | `0b100` |
| 1B | `0b111` | 2B | `0b110` |
| 1C | `0b001` | 2C | `0b000` |
| 1D | `0b011` | 2D | `0b010` |

A useful empirical consequence: for a given transmitter, the **B key's
bus address is the A key's with the first hex nibble incremented
by 4** (that nibble holds the reversed key bits).

The registry-side conversion (`convert_nikobus_address`) maps a stored
24-bit address to the bus form by reversing 21 bits and **adding** the
3-bit key field into the low bits. Because it adds rather than ORs, the
mapping is **not a bijection** — a carry can collapse two distinct
inputs onto one output, so there is no closed-form inverse. Code that
needs to round-trip an observed bus address must keep the bus address
itself as the key (see the note in `discovery/protocol.py`).

---

## 5. Controller inventory and register reads

### Identifying controllers

Controllers answer the broadcast `#A` with:

```
$18 <addr LE:2> 00 <sig> 0F 3F FF <crc>
```

Byte 4 (`sig`) distinguishes the family — verified across three
installs (`const.py: PC_LINK_INVENTORY_SIGNATURE_BYTE`):

| `sig` | Device |
|---|---|
| `0x50` | PC-Link (05-200) |
| `0x40` | PC-Logic (05-201) |

Both answer `#A`, so on installs with both controllers the signature is
what prevents inventory reads from being aimed at the wrong device.

### Register reads

Register reads are framed as `$14…` commands
(`make_pc_link_inventory_command`), function `0x10` plus the target
address and register index — on the wire, reads against a controller at
`<addr>` look like `$1410<addr><reg>04…`. Answers arrive as `$2E…` or
`$1E…` data frames; a **`$18FFFF…` trailer** means "nothing further"
and short-circuits the sweep. Real-hardware timing: ACKs land
300–700 ms after a send (`const.py`, module-scan timeouts).

Every read on a controller-class module returns one **16-byte record**
(`discovery/pc_record_parser.py`). Two record types, discriminated by
shape (byte 0 is *not* install-stable — `0x03` on one PC-Link, `0x04`
on another):

**Registry record** — a module the controller knows:

```
<marker> 00 00 00 <type> 00 00 00 <addr_lo> <addr_hi> 00 00 <slot> 00 00 00
```

**Link record** — a button→output routing entry:

```
<chan> 00 00 00 <mode> 00 00 <flag> <p0> <p1> <p2> 00 <slot> 00 00 00
```

Invariant common to both (used to reject noise): bytes 1–3 are always
`00 00 00`. The PC-Link also emits filler at low register indexes —
byte-ramp "diagnostic echo" pages (`00 01 02 03 …`) and partial-empty
fragments — which must be filtered before parsing, or they seed phantom
devices.

### Registry header and record count

The registry region is preceded by a header page whose tail is the
magic **`5E55AAAA <count>`** — `<count>` is the number of registry
records that follow. This bounds the sweep (no need to scan all 256
registers) and is also where the per-device **Component.Number** lives:
the index the Niko PC software displays as "BP7" / "S1", verified
byte-for-byte against the `.nkb` project file's Component table.

An all-`FF` record means an *empty slot*, *not* end-of-data: real
installs have mid-table gaps (deleted modules). Only the `$18FFFF…`
trailer, the header count, or a run of consecutive empties ends a
sweep.

---

## 6. Logical-input address schemes (05-201 / 05-206)

Controllers with logical inputs compute the bus addresses of those
inputs from their **own module address and a slot index** — the
addresses are algorithmic, not stored anywhere. The two families use
**different firmware formulas**
(`discovery/protocol.py: derive_pc_logic_input_physicals`):

**PC-Logic (05-201)** — validated on three independent installs
(`0x940C`, `0x8DC8`, plus a live 5-input capture on `0x940C`):

```
input_physical = 0x600000 | ((module_addr >> 1) << 4) | slot     # slot = 1..6
```

**Modular Interface (05-206)** — validated on two installs
(`0x940C`-family unit, `0x0548`):

```
input_physical = 0x180000 + module_addr + slot                   # slot = 1..N
```

In both schemes the input's **A bus address** is
`convert_nikobus_address(input_physical)` and the **B bus address** is
the A address with the first nibble `+4` (§4). Worked example,
PC-Logic `0x940C`, slot 1: physical `0x64A061` → A `21814B`,
B `61814B` — matching the live frames byte for byte.

History note: through library 0.33.x the 05-201 formula was applied to
both families, so 05-206 inputs were synthesized on addresses the
hardware never emits (Nikobus-HA issue #485). Hardware captures pinned
the separate 05-206 scheme; fixed in 0.34.0.

---

## 7. Timing

Observed / implemented values (`const.py`):

| What | Value |
|---|---|
| Inter-command delay on the queue | 150 ms |
| Register-read ACK latency (real hardware) | 300–700 ms |
| Command ACK wait ceiling | 15 s |
| Data-after-ACK wait | 1.5 s |
| Simulated press repeat count | ≥2 required by modules; 3 used ("2 to register, 3 to be sure") |

---

## 8. Known unknowns

- The full function-code space of the PC-Link beyond those in §3 is
  unmapped; some codes get no answer at all on some hardware
  generations.
- Link-record byte-0 target indices are only resolved against the
  in-scan registry buffer; the mapping needs more installs' dumps to be
  considered final (`pc_record_parser.py`, Stage 2b note).
- Feedback-module (`$1C`) payloads are decoded only as far as the
  channel states this library needs.
- Field meanings marked "empirical" (e.g. the `0x6` class marker in the
  05-201 input formula) may be refined as more installs are observed.

If you can capture traffic that settles any of these, please open an
issue with the raw trace.
