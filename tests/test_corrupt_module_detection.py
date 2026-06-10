"""Corrupt-module detection — real production frames (module 4707).

Module 4707's link table reads mid-record relative to the scanned
register window (its first frame opens with ``13FF``, the 2-byte tail
of a record preceding the window). On the 2026-06-09 fdebrus scan a
fixed-stride walk produced ~21 phantom buttons and lost every real
record; the Nikobus PC software independently flagged the install as
corrupt and asked for reprogramming. After the user reprogrammed 4707
it read cleanly (proving the misalignment WAS corruption, not the
decoder).

Policy (per maintainer): we do NOT try to recover a corrupt table —
re-aligning a corrupt scan only yields a partial/uncertain picture
(records pushed out of the scanned window are simply gone). Instead we
DETECT it, SKIP its link decode (no phantom buttons), and FLAG the
module so the host can tell the user to reprogram it.

These tests pin that policy with the real frames:
1. with inventory, 4707 is detected misaligned → no buttons decoded;
2. without inventory (no evidence), the walk is unchanged (today's
   behaviour — best-effort, never guesses);
3. an aligned module (9105, same scan) is NOT flagged and decodes.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nikobus_connect.discovery.switch_decoder import SwitchDecoder
from nikobus_connect.discovery.shutter_decoder import ShutterDecoder

# ``payload_and_crc`` exactly as received for the misaligned module 4707.
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

# Aligned control module (9105, roller) from the same scan.
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

# Canonical button addresses of the install (each decodes on an ALIGNED
# module in the same production log).
INSTALL_INVENTORY = {
    "1CFE4A": 4, "1CFBA0": 4, "1DF256": 4, "1DF39E": 4, "1D1EA6": 4,
    "201250": 4, "0FFEC8": 4, "1CA840": 4, "0D1C9E": 4, "1E0D48": 4,
    "0D1C8B": 4, "0D1C8C": 4, "0D1C8D": 4, "0D1C8E": 4, "0D1C90": 4,
    "182F18": 4, "1C8D84": 4, "1DF1E0": 8, "2E58F6": 4, "17F2F8": 4,
}


def _coordinator(*, inventory: dict[str, int] | None = None) -> MagicMock:
    coord = MagicMock()
    if inventory is None:
        coord.get_button_channels = MagicMock(return_value=None)
    else:
        coord.get_button_channels = MagicMock(
            side_effect=lambda addr: inventory.get((addr or "").upper())
        )
    coord.get_module_channel_count = MagicMock(return_value=12)
    return coord


def _replay(decoder, frames: list[str]):
    """Run frames through the production path. Returns (decoded, misaligned)."""
    decoded = []
    misaligned = False
    buffer = ""
    for frame in frames:
        analysis = decoder.analyze_frame_payload(buffer, frame)
        assert analysis is not None
        buffer = analysis["remainder"]
        misaligned = misaligned or bool(analysis.get("misaligned"))
        for chunk in analysis["chunks"]:
            decoded.extend(decoder.decode_chunk(chunk) or [])
    return decoded, misaligned


def _buttons(decoded) -> set[str]:
    return {
        (d.metadata or {}).get("button_address")
        for d in decoded
        if (d.metadata or {}).get("button_address")
    }


def test_corrupt_module_is_detected_and_skipped() -> None:
    """With inventory, 4707 is flagged misaligned and NOTHING is decoded
    — no phantom buttons enter the store."""
    decoder = SwitchDecoder(_coordinator(inventory=INSTALL_INVENTORY))
    decoder.set_module_address("4707")
    decoder.set_module_channel_count(12)

    decoded, misaligned = _replay(decoder, FRAMES_4707)

    assert misaligned is True
    assert decoded == [], (
        f"corrupt module must decode nothing, got {_buttons(decoded)}"
    )


def test_no_inventory_does_not_flag_or_change_behaviour() -> None:
    """Without inventory to score against, the decoder never guesses:
    no misaligned flag, and the walk is the plain offset-0 behaviour."""
    decoder = SwitchDecoder(_coordinator())
    decoder.set_module_address("4707")
    decoder.set_module_channel_count(12)

    decoded, misaligned = _replay(decoder, FRAMES_4707)

    assert misaligned is False
    assert decoded  # plain offset-0 walk still produces its (phantom) output


def test_aligned_module_is_not_flagged_and_decodes() -> None:
    """The aligned control module is not flagged and decodes its real
    buttons normally."""
    decoder = ShutterDecoder(_coordinator(inventory=INSTALL_INVENTORY))
    decoder.set_module_address("9105")
    decoder.set_module_channel_count(6)

    decoded, misaligned = _replay(decoder, FRAMES_9105)
    buttons = _buttons(decoded)

    assert misaligned is False
    assert {"1CFBA0", "1DF256"} <= buttons
    assert "0D1C8B" in buttons
