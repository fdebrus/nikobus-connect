"""The colour family's device-type bytes, from product.mdb's keys.

The type byte a component is filed under in the PC-Link registry is
``KeyProductBase``, the primary key of product.mdb's ``ProductBase``
(checked over the whole catalogue on 2026-09-30: 28 of 33 catalogued
bytes name the product row with that key; the five others are the
catalogue's own aliases). The 340-00112 controller is key 70 = 0x46;
its neighbours are key 69, the 340-00111 plinth light, and key 71,
the same controller in its mono profile. Type 0x45 was first reported
on a real install on 2026-09-30, at a 16-bit address, as unknown.
"""

from __future__ import annotations

from nikobus_connect.discovery.mapping import (
    DEVICE_TYPES,
    get_module_type_from_device_type,
)


def test_the_plinth_light_is_type_0x45() -> None:
    entry = DEVICE_TYPES["45"]
    assert entry["Category"] == "Module"
    assert entry["Model"] == "340-00111"
    assert entry["VendorRef"] == "S_DB_DIM_PLINT"


def test_the_mono_controller_is_type_0x47() -> None:
    entry = DEVICE_TYPES["47"]
    assert entry["Category"] == "Module"
    assert entry["Model"] == "340-00112"
    assert entry["VendorRef"] == "S_DB_DIM_MONOCTRL"


def test_the_family_sits_on_consecutive_keys() -> None:
    assert [DEVICE_TYPES[b]["VendorRef"] for b in ("45", "46", "47")] == [
        "S_DB_DIM_PLINT",
        "S_DB_DIM_COLORCTRL",
        "S_DB_DIM_MONOCTRL",
    ]


def test_neither_new_byte_reaches_the_rgb_light_platform_yet() -> None:
    """Inventory only: no state reply from either has been captured."""
    assert get_module_type_from_device_type("46") != "other_module"
    assert get_module_type_from_device_type("45") == "other_module"
    assert get_module_type_from_device_type("47") == "other_module"


def test_no_new_byte_has_a_channel_count() -> None:
    """A channel count would have the host build an entity for a load
    nothing can read or drive yet."""
    assert "Channels" not in DEVICE_TYPES["45"]
    assert "Channels" not in DEVICE_TYPES["47"]
