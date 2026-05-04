"""Phantom-rejection guard for the per-module decoders.

Real-hardware register scans return chunks where the last 3 bytes
sometimes land on routing / cell-prefix bytes (rather than a real
button-link record). Those decode to canonical "button addresses"
that match no entry in the live inventory. Pre-0.5.4 they reached
the merge layer, got logged as ``unmatched``, and bloated the per-scan
log without ever contributing a real ``linked_modules`` entry.

The guard rejects them at decode time. Three invariants pinned here:

1. Phantom canonical addresses (not in inventory, not a +1 sibling)
   produce ``None`` from each output-module decoder.
2. Real canonical addresses (direct inventory hit) decode normally.
3. The ``+1`` alias case for 8-channel buttons survives — half the
   keys of an 8-ch button decode to ``inventory_addr + 1`` and the
   merge layer aliases that case via ``_build_bus_to_op_index``.
   The decoder must NOT drop those.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nikobus_connect.discovery import dimmer_decoder, shutter_decoder, switch_decoder
from nikobus_connect.discovery.protocol import (
    DecoderContext,
    is_known_button_canonical,
    normalize_payload,
)


def _ctx(coordinator, *, module_address: str = "4707", channels: int = 12) -> DecoderContext:
    return DecoderContext(
        coordinator=coordinator,
        module_address=module_address,
        module_channel_count=channels,
    )


# ---------------------------------------------------------------------------
# is_known_button_canonical: unit
# ---------------------------------------------------------------------------


def test_is_known_button_accepts_direct_inventory_hit():
    """Canonical addresses present in the live inventory pass through."""

    def get_channels(addr: str):
        return {"1843B4": 4}.get(addr.upper())

    assert is_known_button_canonical("1843B4", get_channels) is True


def test_is_known_button_accepts_8ch_plus_one_alias():
    """8-channel ``inventory_addr + 1`` siblings pass through.

    Half the keys (raw indices 4-7) of an 8-channel button decode to
    canonical = ``inventory_addr + 1``. The bus_to_op index aliases
    that case at merge time, so the decoder must not drop it.
    """

    def get_channels(addr: str):
        return {"1DF1E0": 8}.get(addr.upper())

    # 1DF1E1 isn't a direct inventory entry, but 1DF1E0 (its -1
    # sibling) is an 8-channel button → alias matches.
    assert is_known_button_canonical("1DF1E1", get_channels) is True


def test_is_known_button_rejects_phantom_address():
    """Canonicals matching no inventory entry (and no +1 alias) are rejected."""

    def get_channels(addr: str):
        return {"1843B4": 4, "1DF1E0": 8}.get(addr.upper())

    # Phantom canonicals from the fdebrus/other-user logs:
    for phantom in ("162C88", "202C8E", "203844", "3FFFFF"):
        assert is_known_button_canonical(phantom, get_channels) is False, phantom


def test_is_known_button_does_not_alias_4ch_neighbours():
    """4-channel buttons don't get the +1 alias treatment (only 8-ch do)."""

    def get_channels(addr: str):
        return {"1843B4": 4}.get(addr.upper())

    # 1843B5 = 1843B4 + 1, but 1843B4 is 4-channel → no alias.
    assert is_known_button_canonical("1843B5", get_channels) is False


def test_is_known_button_lenient_when_no_coordinator():
    """Without a coordinator (test harness, bare-metal tooling), accept all
    addresses — the guard exists to filter against a *live* inventory,
    not to enforce one."""

    assert is_known_button_canonical("1843B4", None) is True
    assert is_known_button_canonical("162C88", None) is True


# ---------------------------------------------------------------------------
# Decoder integration: phantom rejection
# ---------------------------------------------------------------------------


def _coordinator_with_inventory(inventory: dict[str, int]) -> MagicMock:
    """Mock coordinator whose ``get_button_channels`` returns ``inventory``."""

    coord = MagicMock()
    coord.get_button_channels = MagicMock(
        side_effect=lambda addr: inventory.get((addr or "").upper())
    )
    return coord


def test_switch_decoder_drops_phantom_button_address():
    """Switch chunks whose last 3 bytes land on routing data → None."""

    # Last 3 bytes = 20B258 → canonical 162C88 (a phantom from the logs).
    payload = "0F0235" + "20B258"
    raw = normalize_payload(payload)
    coord = _coordinator_with_inventory({"1843B4": 4})

    result = switch_decoder.decode(payload, raw, _ctx(coord))
    assert result is None


def test_shutter_decoder_drops_phantom_button_address():
    payload = "0F0235" + "20B258"  # canonical 162C88 phantom
    raw = normalize_payload(payload)
    coord = _coordinator_with_inventory({"1843B4": 4})

    result = shutter_decoder.decode(payload, raw, _ctx(coord))
    assert result is None


def test_dimmer_decoder_drops_phantom_button_address():
    # 16-hex chunk; last 6 = 30B280 → canonical 202C8C (phantom).
    payload = "AABBCC" + "0900FF" + "30B280" + "0000"
    # Pad so the chunk is exactly 16 hex chars.
    payload = (payload + "00" * 8)[:16]
    raw = normalize_payload(payload)
    coord = _coordinator_with_inventory({"1843B4": 4})

    result = dimmer_decoder.decode(payload, raw, _ctx(coord))
    assert result is None


def test_switch_decoder_keeps_real_button_address():
    """When the last 3 bytes encode a real canonical, decoding succeeds."""

    # 80EE73 → canonical 1CFBA0 (a real button, see fdebrus working config).
    payload = "0530B2" + "80EE73"
    raw = normalize_payload(payload)
    coord = _coordinator_with_inventory({"1CFBA0": 4})

    result = switch_decoder.decode(payload, raw, _ctx(coord, channels=12))
    assert result is not None
    assert result["button_address"] == "1CFBA0"


def test_switch_decoder_keeps_8ch_plus_one_alias():
    """The +1 sibling of an 8-channel button must NOT be dropped."""

    # 84C777 → canonical 1DF1E1 (the +1 sibling of 8-ch button 1DF1E0).
    payload = "0001F0" + "84C777"
    raw = normalize_payload(payload)
    coord = _coordinator_with_inventory({"1DF1E0": 8})

    result = switch_decoder.decode(payload, raw, _ctx(coord, channels=12))
    assert result is not None
    assert result["button_address"] == "1DF1E1"


def test_decoders_run_without_coordinator_button_api():
    """Bare-metal coordinator without ``get_button_channels`` → no guard
    applied (guard is lenient when the API is missing).

    Mirrors the test-harness path: harnesses don't supply a button
    inventory so the decoder must still produce records.
    """

    coord = MagicMock(spec=[])  # no get_button_channels attribute

    # Real chunk: 80EE73 → canonical 1CFBA0
    payload = "0530B2" + "80EE73"
    raw = normalize_payload(payload)

    result = switch_decoder.decode(payload, raw, _ctx(coord, channels=12))
    assert result is not None
    assert result["button_address"] == "1CFBA0"
