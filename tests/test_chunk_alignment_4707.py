"""Replay harness: register-scan chunk alignment, real frames from a
production scan log (module 4707, switch; module 9105, roller — the
2026-06-09 fdebrus install log).

Module **4707**'s link table starts mid-record relative to the scanned
register window: the first response frame opens with ``13FF`` — the
tail (timer + filler) of a record that *precedes* the window. The
fixed-stride chunk walk never re-aligns, so every 12-hex window is
phase-shifted by 4 hex for the whole scan. Observable blast radius in
the production log:

* ~21 *phantom* buttons decode "cleanly" (``0C025D``, ``0C42DD``, … —
  near-consecutive addresses, identical key/channel/mode), all landing
  in the unmatched accumulator;
* the module's REAL records — buttons ``80EE73`` (canonical ``1CFBA0``)
  and ``77C958`` — are never decoded on 4707 (their windows decode to
  "unknown mode" or melt into the phantoms), so 4707's controlled_by
  is silently incomplete.

Module **9105** (same buttons, table aligned with the window) decodes
perfectly and serves as the control group.

These tests pin three facts:
1. the current walk reproduces the production misalignment exactly
   (characterization — this is the bug, asserted as today's behaviour);
2. the same byte stream shifted by the 4 orphan hex chars decodes the
   REAL records (proof of what a re-alignment fix must recover);
3. the aligned control module is unaffected.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nikobus_connect.discovery.switch_decoder import SwitchDecoder
from nikobus_connect.discovery.shutter_decoder import ShutterDecoder

# ``payload_and_crc`` exactly as received for module 4707 (the part of
# each $2E frame after the "$2E" + bus-address prefix), in arrival order.
FRAMES_4707 = [
    "13FF73F928F0010473F928F004FF787B654049",
    "20F0161B77C784F007FF73EE80023009449AF1",
    "77C95802200A73EE8002310B77C9580264D20C",
    "210C73EE8002330D77C95802230E73EEFB8A12",
    "8002340F77C95802241073EE80023611EE8DD6",
    "77C95802261273EE800237FF77C95802A3EE53",
    "27FF796130F018FF77CE78F009FF747AEB238D",
    "98F01AFF804940F00AFF3FFB00F002180FED14",
    "3FFB00F005FF3FFB2002121A3FFB2002477042",
    "15FF72A100F01A1C72A100F00A1D72A116EB8E",
    "00F00BFF347278013BFFFFFFFFFFFFFFF256BA",
]

# Control group: module 9105 (roller), same scan, decodes correctly.
FRAMES_9105 = [
    "34722CE012FF347230E014FF347234B060A1B9",
    "16FF347238B018FF60BC60B00AFF3472EDFFF2",
    "40B01AFF73EE80E2320877C958E222092D8CC1",
    "73EE80E2340A77C958E2240B73EE80B28C89F0",
    "360C77C958B2260D73EE80B2380E77C9B72437",
    "58B2280F73EE80B23A1177C958B22AFF7D78DA",
    "804940E012FF73EE80E1121273EE80E133BF52",
    "141373EE80B1161473EE80B1181573EE57D17D",
    "80B11AFF812454E014FF747C48B008FFCA71D7",
    "610ED0E0021A347228E0121B610ED0E0B468EA",
    "041C347228E0141D610ED0B0061E3472385BA8",
    "28B0161F610ED0B008FF347228B018FFC88138",
    "347200B016FFFFFFFFFFFFFFFFFFFFFFA695E9",
]

# The two REAL buttons programmed into both tables (confirmed: they
# decode on 9105 / 0E6C / 5B05 / C9A5 in the same log; 1CFBA0 is the
# canonical form of bus address 80EE73).
REAL_BUTTONS = {"1CFBA0", "1DF256"}  # 73EE80 / 77C958 reversed+canonical

# A few of the phantom addresses the production log shows for 4707.
PRODUCTION_PHANTOMS = {"0C025D", "0C42DD", "0CC35D", "0D03DD", "0D845D"}


def _coordinator() -> MagicMock:
    coord = MagicMock()
    coord.get_button_channels = MagicMock(return_value=None)
    coord.get_module_channel_count = MagicMock(return_value=12)
    return coord


def _replay(decoder, frames: list[str]) -> list:
    """Run frames through the production path: analyze → chunk → decode."""
    decoded: list[dict] = []
    buffer = ""
    for frame in frames:
        analysis = decoder.analyze_frame_payload(buffer, frame)
        assert analysis is not None
        buffer = analysis["remainder"]
        for chunk in analysis["chunks"]:
            decoded.extend(decoder.decode_chunk(chunk) or [])
    return decoded


def _buttons(decoded: list) -> set[str]:
    out = set()
    for d in decoded:
        meta = getattr(d, "metadata", None) or {}
        addr = meta.get("button_address")
        if addr:
            out.add(addr)
    return out


def test_4707_misalignment_reproduces_production_log() -> None:
    """Characterization of the BUG: the fixed-stride walk on 4707's
    mid-record window yields the production phantoms and misses the
    real buttons entirely."""
    decoder = SwitchDecoder(_coordinator())
    decoder.set_module_address("4707")
    decoder.set_module_channel_count(12)

    decoded = _replay(decoder, FRAMES_4707)
    buttons = _buttons(decoded)

    # The phantoms from the production log reproduce…
    assert PRODUCTION_PHANTOMS & buttons, (
        f"expected production phantoms in {sorted(buttons)}"
    )
    # …and the module's REAL buttons are entirely absent.
    assert not (REAL_BUTTONS & buttons), (
        f"real buttons unexpectedly decoded: {REAL_BUTTONS & buttons}"
    )


def test_4707_realigned_stream_recovers_the_real_records() -> None:
    """Proof of recoverability: dropping the 4 orphan hex chars (the
    ``13FF`` record-tail) before the walk decodes the REAL buttons with
    plausible records — this is what an alignment fix must achieve."""
    decoder = SwitchDecoder(_coordinator())
    decoder.set_module_address("4707")
    decoder.set_module_channel_count(12)

    frames = [FRAMES_4707[0][4:]] + FRAMES_4707[1:]
    decoded = _replay(decoder, frames)
    buttons = _buttons(decoded)

    assert REAL_BUTTONS & buttons, (
        f"re-aligned stream should decode the real buttons, got {sorted(buttons)}"
    )
    # And the production phantoms disappear.
    assert not (PRODUCTION_PHANTOMS & buttons), (
        f"phantoms survived re-alignment: {PRODUCTION_PHANTOMS & buttons}"
    )


def test_9105_control_group_decodes_real_buttons() -> None:
    """Aligned table (same scan, same buttons) — sanity control."""
    decoder = ShutterDecoder(_coordinator())
    decoder.set_module_address("9105")
    decoder.set_module_channel_count(6)

    decoded = _replay(decoder, FRAMES_9105)
    buttons = _buttons(decoded)

    assert REAL_BUTTONS <= buttons, (
        f"control group should decode the real buttons, got {sorted(buttons)}"
    )
