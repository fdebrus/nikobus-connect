"""Device type 0x46 — the 340-00112 RGB controller.

Identified 2026-09-28 (Nikobus-HA #519) from an install whose PC-Link
registry reported type 0x46 at address 801D and whose .nkb names the
component at physical address 32797 — the same number — as "RGB
Controller (kleur mode)", product 340-00112, vendor ref
``S_DB_DIM_COLORCTRL``. Two independent sources agreeing on the address
is what makes it a match rather than a guess.

What is catalogued is the identity only. Nobody has dumped the module's
registers, so where it keeps its programming, how its output reads back
and what drives it are all unknown — and the tests below pin that
restraint as much as the entry itself.
"""

from __future__ import annotations

from nikobus_connect.discovery.discovery import NON_OUTPUT_MODULE_TYPES
from nikobus_connect.discovery.discovery import (
    _scan_passes_for_module_type as scan_passes,
)
from nikobus_connect.discovery.fileio import (
    DESCRIPTION_PREFIX,
    merge_discovered_modules,
)
from nikobus_connect.discovery.mapping import (
    DEVICE_TYPES,
    get_module_type_from_device_type,
)

DEVICE_TYPE = "46"


def test_the_controller_is_catalogued_from_the_project_file() -> None:
    entry = DEVICE_TYPES[DEVICE_TYPE]
    assert entry["Category"] == "Module"
    assert entry["Model"] == "340-00112"
    assert entry["Name"] == "RGB controller"
    assert entry["VendorRef"] == "S_DB_DIM_COLORCTRL"


def test_it_claims_no_channels() -> None:
    """The Niko software shows one output, but nothing is known about
    how that output is read or driven — so no channel count, and no host
    builds an entity for a load it cannot control."""
    assert "Channels" not in DEVICE_TYPES[DEVICE_TYPE]


def test_it_gets_its_own_bucket() -> None:
    assert get_module_type_from_device_type(DEVICE_TYPE) == "rgb_module"
    assert DESCRIPTION_PREFIX["rgb_module"] == "rgb_module_"


def test_it_is_not_scanned_until_someone_dumps_its_registers() -> None:
    """Scanning a module whose layout nobody knows is guesswork on a
    real bus: 40-odd reads that either time out or decode noise."""
    assert "rgb_module" in NON_OUTPUT_MODULE_TYPES
    assert scan_passes("rgb_module") == ()
    assert scan_passes("rgb_module", broad_scan=True) == ()


def test_it_reaches_the_module_store_instead_of_being_dropped() -> None:
    """While it sat as Reserved, the module was dropped from every
    inventory — the merge only acts on Module and Button categories."""
    module_data: dict = {}
    added, updated = merge_discovered_modules(
        module_data,
        {
            "801D": {
                "address": "801D",
                "category": "Module",
                "module_type": "rgb_module",
                "model": "340-00112",
                "device_type": DEVICE_TYPE,
                "discovered_name": "RGB controller",
            }
        },
    )
    assert (added, updated) == (1, 0)
    entry = module_data["nikobus_module"]["801D"]
    assert entry["module_type"] == "rgb_module"
    assert entry["model"] == "340-00112"
    assert entry["description"] == "rgb_module_1"
    # No channels array: there is nothing to build entities from yet.
    assert "channels" not in entry


# --- the link modes, from the vendor's programming leaflet ----------------


def test_the_mode_table_is_the_leaflets_fifteen_modes() -> None:
    """PM340-00112 lists 15 modes: nine shared with the dimmer, six
    colour-only. The dimmer's M02/M03/M09/M10/M11/M14 do not exist here."""
    from nikobus_connect.discovery.mapping import RGB_MODE_NAMES, RGB_MONO_MODES

    assert set(RGB_MODE_NAMES) == {1, 4, 5, 6, 7, 8, 12, 13, 15, 16, 17, 18, 19, 20, 21}
    assert RGB_MONO_MODES == {1, 4, 5, 6, 7, 8, 12, 13, 15}
    assert RGB_MONO_MODES < set(RGB_MODE_NAMES)


def test_the_validating_installs_link_is_start_stop_scenario() -> None:
    """The one link the #519 install has on its controller is M19: the
    upper key starts/stops the scenario, the lower one dims off."""
    from nikobus_connect.discovery.mapping import RGB_MODE_KEYS, rgb_mode_label

    assert rgb_mode_label(19) == "M19 (Start/stop scenario)"
    assert RGB_MODE_KEYS[19] == 2


def test_the_factory_default_is_the_four_key_colour_mode() -> None:
    from nikobus_connect.discovery.mapping import RGB_MODE_KEYS, rgb_mode_label

    assert rgb_mode_label(16) == "M16 (Set colour and luminance)"
    assert RGB_MODE_KEYS[16] == 4


def test_a_mode_off_the_table_keeps_its_number() -> None:
    from nikobus_connect.discovery.mapping import rgb_mode_label

    assert rgb_mode_label(2) == "M02"


def test_every_mode_has_a_key_count_and_a_vendor_ref() -> None:
    from nikobus_connect.discovery.mapping import (
        RGB_MODE_KEYS,
        RGB_MODE_NAMES,
        RGB_MODE_VENDOR_REF,
    )

    assert set(RGB_MODE_KEYS) == set(RGB_MODE_NAMES) == set(RGB_MODE_VENDOR_REF)
    assert set(RGB_MODE_KEYS.values()) == {1, 2, 4}
    for number, ref in RGB_MODE_VENDOR_REF.items():
        assert ref == f"S_DB_DESC_DIMMER_COLOR_M{number}"


def test_the_link_record_byte_is_still_unknown() -> None:
    """The table is keyed by mode number. No link record of this module
    has been read, so the byte it stores is unobserved and the PC-Link
    record parser must not pretend otherwise."""
    from nikobus_connect.discovery.pc_record_parser import _MODE_TABLE_BY_DEVICE_TYPE

    assert 0x46 not in _MODE_TABLE_BY_DEVICE_TYPE
