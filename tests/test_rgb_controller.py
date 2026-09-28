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
