# What the vendor software taught us

A record of the September 2026 reverse engineering of Niko's Nikobus PC
software, kept next to [`PROTOCOL.md`](../PROTOCOL.md) so that the
*method* and the *dead ends* survive along with the facts. The facts
themselves live in `PROTOCOL.md`; the block-by-block memory maps of
each product family live in the Nikobus-HA repository
(`documentation/vendor-plugins-memory-maps.md`,
`documentation/rgb-controller-memory-map.md`), next to the binaries.

Nikobus is a trademark of Niko NV. Nothing here is endorsed by or
sourced from Niko; every statement below was read from the software's
own machine code or database, or observed on a real bus.

## 1. What was analysed

| File | Version / size | What it is |
|---|---|---|
| `nikobus.exe` | 4.3.1 | The PC software: bus framing and CRC-16, the programming sequence, the "load existing installation" upload, the simulation mode, the project database logic |
| `serial.dll` | shipped with 4.3.1 | The serial layer: `$`-framing, the CRC-8, the carriage return, port handling |
| `Niko_05_000_01.dll` | idem | Plugin for the switch (05-000-02, 05-002-02) and roller (05-001-02) modules |
| `Niko_05_007.dll` | idem | Plugin for the dimmers (05-007-02, 05-008-02) |
| `Niko_05_010.dll` | idem | Plugin for the colour family (340-00112 colour and mono, 340-00111, 340-00113) |
| `Niko_05_100.dll` | idem | Plugin for the PC-Link (05-200) |
| `Niko_05_200.dll` | idem | Plugin for the PC-Logic (05-201) |
| `Niko_05_202.dll` | idem | Plugin for the audio distribution module (05-205) |
| `Niko_05_207.dll`, `Niko_05_207a.dll` | idem | Plugins for the feedback module (05-207); `207a` builds the report only |
| `product.mdb` | `DatabaseVersion` 21008 | The product database: products, object types, link modes, parameters |

Not analysed: `Niko_05_201a.dll` (the SMS module, memory class 4 like
the PC-Logic) and the remaining UI-only libraries.

The binaries are kept in the Nikobus-HA repository's `documentation/`
folder (`Niko_05_010.dll`, and `nikobus.exe` as `nikobus.exe.txt`).

## 2. How

- **Decompilation**: Ghidra 11.3.2, headless, with a small script that
  decompiles every function of a binary to one C file; radare2 for the
  stretches of `nikobus.exe` where exception handlers broke Ghidra's
  function boundaries; `pefile` for exports and imports.
- **The database**: read with the library's own Access reader
  (`nikobus_connect.nkb._access_parser`, the one the `.nkb` parser uses),
  tables `ProductBase`, `ObjectTypeBase`, `LinkModeBase`, `ParamBase`,
  `ObjectBase`, `TypeTabel`.
- **Cross-checks against reality**: the record layouts read from the
  plugins' composers were compared with the library's decoders, which
  were themselves built from dumps of real modules; a validating
  install's `#N` frames settled the address form (§4 of `PROTOCOL.md`).

## 3. The plugin interface

Each product family the software can program is one DLL with the same
exports:

| Export | Role |
|---|---|
| `CalcMemoryMap(component, block, …)` | Builds one block of the family's memory image for a component, from the project database |
| `GetDLLReadInfo(block, …)` | Tells the software each block's byte address, length and block size, and how much of it to read back on upload |
| `GetDLLReadWriteInfo` | Two further ranges per block; meaning not established |
| `TranslateUpload(component, data, …)` | Decodes an upload (a module read back from the bus) into database rows |
| `GetPreReport` | The HTML report of a component's links |
| `FreeMemory` | Frees a block |

The executable owns everything else: the function codes, the link
mode, the clear, the CRC check, the retries, and the upload loop that
walks the project tree (`TVM_GETNEXTITEM`) and skips memory classes 10,
11 and 12 before sending a frame.

## 4. What each file settled

- **`serial.dll`**: the frame is `$`, a length byte as two hex digits,
  the payload as hex, a CRC-8 over the frame text (its "checksum type
  2", polynomial `0x99`), and a carriage return. That CRC-8 is the
  library's `calc_crc2`; the CRC-16 (`0x1021` from `0xFFFF`) is computed
  in the executable and is the library's `calc_crc1`. Retries are the
  executable's: up to five sends per attempt, a wait of 50 ms × 5 × a
  per-call factor with a floor of one second, a growing pause between
  attempts.
- **`nikobus.exe`**: the programming sequence (link mode on, clear,
  non-empty 16-byte blocks written, link mode on again, CRC compared,
  link mode off; PC-Logic and PC-Link bracketed with memory-invalid /
  memory-valid instead and not CRC-checked; the audio module not
  CRC-checked; the feedback module and the colour family kept in link
  mode for the whole write). The `EEPROMtype` column of `ProductBase` is
  the "memory class" it dispatches on. Its simulation mode presses a
  key by bit-reversing `plate << 2 | key_code` into a `#N` frame, and
  has no direct command for a dimmer or colour output.
- **`Niko_05_000_01`**: one block from byte `0x100`, 6-byte records,
  read back count-driven; the record as composed
  (`PROTOCOL.md` §7), the hash as the byte sum of the address; the mode
  nibble holds `LinkModeBase.LinkIDNumber`; its upload decoder flags a
  record as a sequencer exactly when that nibble is 10.
- **`Niko_05_007`**: banks at `0x100` and `0x900`, 8-byte blocks, the
  configuration block at `0x7CA` (48 bytes) written fixed. The
  library's plan, to the block.
- **`Niko_05_010`**: the 7968-byte colour image, the 18-byte link
  record, the colour paths, the settings record; read-back length zero
  for the link table; its `TranslateUpload` is dimmer code and cannot
  read the records it writes.
- **`Niko_05_100`**: the PC-Link image (header, presence simulation,
  calendar channels, appointments, status band, registry at 19000 with
  the `5E 55 AA AA` header) — the registry is the one block the plugin
  never reads back.
- **`Niko_05_200`**: the PC-Logic image, including the input table at
  998 (the links whose output is the PC-Logic) and the CF
  trigger-address grid at 11000 the library already recognised from a
  real scan.
- **`Niko_05_202`**: the audio image (count at 998 and 4998, index
  table at 1000, records at 5000, settings at 100); the upload decoder's
  validity rule (sixth byte 1) and field positions match the library's
  decoder; capacity 1864 where the library read about 105.
- **`Niko_05_207`**: the feedback module's `0x7900`-byte image in five
  blocks; nothing readable outside link mode.
- **`product.mdb`**: the memory classes and which product carries
  which; `LinkIDNumber` as the mode byte for every family; the products
  with no catalogue entry in the library (SMS module 05-203, remotes
  05-081 / 05-085, RF plates 05-305 / 05-310 / 05-313, RF boxes 05-315,
  modular interface 05-055, PIR 05-045, outdoor sensor 430-00502, smoke
  detector 420-00005, plinth light 340-00111). It carries no bus
  device-type byte; `TypeInfo` is a UI class.

## 5. Dead ends, recorded so nobody walks them twice

- **The colour controller's link table cannot be read from the bus.**
  The vendor writes it and never reads it back: the upload routine
  skips memory classes 10, 11 and 12, and the plugin's read-back length
  for the table is zero. On the bus the controller answers nothing but
  the output-state query outside link mode. Whether it answers block
  reads *inside* link mode is untested and stays so from this library:
  entering link mode is a programming command. The keys linked to a
  controller therefore come from the `.nkb` project file
  (`parse_nkb().rgb_links`), or from watching which key changes the
  controller's state.
- **"Load existing installation" does not report a colour link.** It
  status-polls the controller and writes a project file with its
  settings and no links; a challenge to this was checked against the
  executable and confirmed.
- **The feedback module** is the same story (memory class 10): its LED
  table is written in link mode and never read back.
- **There is no direct colour command.** The vendor's own simulation
  presses the linked key.
- **`Niko_05_010`'s `TranslateUpload` is dimmer code**, 8-byte records,
  217 slots: it is not a decoder for the 18-byte records and must not
  be read as one.

## 6. What changed in the library because of it

| Release | Change |
|---|---|
| 0.42.0 | The colour image documented; a decode-only `rgb_memory` module added |
| 0.42.1 | `rgb_memory` removed again: nothing in a host can feed it without a programming command, so the knowledge went to the documentation instead |
| 0.43.0 | Switch modes M13, M14, M15 read from bytes 10, 11, 12 (`LinkIDNumber`), where the table had 10 = M14 and 11 = M15 and nothing for 12 |
| 0.44.0 | The audio link table read to its two-byte count instead of a fixed band; the PC-Logic input table read in full and reported; a decoder can size its own read (`extension_passes`) |

Everything about *writing* to a module — link mode, clear, block
writes, memory valid / invalid — is documented and not implemented, by
decision: this library reads state and presses keys.

## 7. What a capture could still settle

- A switch-module record with mode nibble 10 (a sequencer link): what
  its channel nibble designates.
- A PC-Logic input table from an install with keys feeding a PC-Logic:
  confirms bytes 3–5 (input index, slot, mode) and unlocks turning
  those keys into entities.
- An audio module with more than 105 links: confirms the extension
  read end to end.
- The meaning of the dimmer's 48-byte configuration block, of the
  PC-Link header flag at byte `0x28`, and of the audio settings block.
- Whether the colour controller or the feedback module answer block
  reads inside link mode (from a capture of the vendor software, never
  from this library).
