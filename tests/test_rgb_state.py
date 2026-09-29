"""The 340-00112's state image and its keys.

Calibrated on a real controller (Nikobus-HA #519): the colour loop was
frozen with the wall key and the state read after each colour. The four
readings below are the captured frames, byte for byte.
"""

from __future__ import annotations

import pytest

from nikobus_connect.rgb import (
    RGB_MODE_KEY_ROLES,
    RGB_OFF_ROLES,
    RGB_ON_ROLES,
    RgbState,
    decode_rgb_state,
    rgb_key_position,
    rgb_key_role,
    rgb_mode_label,
)
from nikobus_connect.discovery.mapping import RGB_MODE_KEYS, RGB_MODE_NAMES


@pytest.mark.parametrize(
    ("image", "expected", "name"),
    [
        ("FF00FFFF0000", RgbState(True, False, True, True), "cyan"),
        ("FFFFFF000000", RgbState(True, True, True, False), "yellow"),
        ("FFFFFFFF0000", RgbState(True, True, True, True), "white"),
        ("FFFF00FF0000", RgbState(True, True, False, True), "magenta"),
        ("000000000000", RgbState(False, False, False, False), "off"),
    ],
)
def test_the_captured_frames_decode_to_flags(image: str, expected: RgbState, name: str) -> None:
    state = decode_rgb_state(image)
    assert state == expected
    assert state.colour_name == name


def test_the_flags_become_an_rgb_triple() -> None:
    assert decode_rgb_state("FFFF00FF0000").rgb == (255, 0, 255)
    assert decode_rgb_state("00FFFFFF0000").rgb == (0, 0, 0)  # off wins


def test_bytes_and_hex_decode_alike() -> None:
    assert decode_rgb_state(bytes.fromhex("FF00FFFF0000")) == decode_rgb_state("FF00FFFF0000")
    assert decode_rgb_state(bytearray(b"\xff\x00\xff\xff\x00\x00")).blue


def test_a_short_or_bad_image_is_off() -> None:
    assert not decode_rgb_state("").on
    assert not decode_rgb_state("FF").on
    assert not decode_rgb_state("ZZZZZZZZZZZZ").on
    assert not decode_rgb_state(b"\xff").on


def test_any_level_lights_a_flag() -> None:
    """Blue at 7 % green read FF for green on the real module; a value in
    between would still be an output that is on."""
    assert decode_rgb_state("FF0012000000") == RgbState(True, False, True, False)


# --- keys -----------------------------------------------------------------


def test_every_mode_has_roles_for_every_key_it_takes() -> None:
    positions_for = {1: 1, 2: 2, 4: 4}
    assert set(RGB_MODE_KEY_ROLES) == set(RGB_MODE_NAMES)
    for mode, roles in RGB_MODE_KEY_ROLES.items():
        assert len(roles) == positions_for[RGB_MODE_KEYS[mode]], mode


def test_the_validating_installs_keys() -> None:
    """Plate 124A36, mode 19: key 1C starts and stops the colour loop,
    key 1D switches off — confirmed on the bus."""
    assert rgb_key_role(19, "1C") == "start_stop"
    assert rgb_key_role(19, "1D") == "off"
    assert rgb_mode_label(19) == "M19 (Start/stop scenario)"


@pytest.mark.parametrize(
    ("mode", "label", "role"),
    [
        (1, "A", "on"),
        (1, "1B", "off"),
        (16, "1A", "colour_next"),
        (16, "1B", "colour_prev"),
        (16, "1C", "on"),
        (16, "2D", "off"),
        (13, "1D", "toggle"),
        (21, "A", "toggle"),
        (20, "2B", "start"),
        (18, "1B", "off"),
    ],
)
def test_a_key_label_maps_to_its_role(mode: int, label: str, role: str) -> None:
    assert rgb_key_role(mode, label) == role


def test_a_mode_off_the_table_or_a_non_key_has_no_role() -> None:
    assert rgb_key_role(2, "1A") is None
    assert rgb_key_role(19, "IR:30A") is None
    assert rgb_key_role(19, "") is None
    assert rgb_key_position(19, "O01") is None


def test_the_on_and_off_role_lists_cover_what_a_host_can_press() -> None:
    assert RGB_ON_ROLES[0] == "on"
    assert RGB_OFF_ROLES == ("off", "toggle")
    every_role = {r for roles in RGB_MODE_KEY_ROLES.values() for r in roles.values()}
    assert set(RGB_ON_ROLES) <= every_role
    assert set(RGB_OFF_ROLES) <= every_role
