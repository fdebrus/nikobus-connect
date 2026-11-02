"""Nikobus Discovery - device-discovery subpackage of nikobus-connect."""

from .discovery import NikobusDiscovery
from .base import (
    DecodedCommand,
    Decoder,
    DiscoveryProgress,
    InventoryQueryType,
    InventoryResult,
    PHASE_FINALIZING,
    PHASE_IDENTITY,
    PHASE_INVENTORY,
    PHASE_REGISTER_SCAN,
)
from .discovery import NON_OUTPUT_MODULE_TYPES
from .fileio import (
    IR_OP_POINT_PREFIX,
    find_ir_operation_point,
    find_module,
    find_operation_point,
    has_per_plate_key_layout,
    two_button_bus_addresses,
)
from .mapping import (
    DEVICE_TYPE_KEY_MAPPING,
    MOTION_DETECTOR_KEY_ROLES,
    PER_PLATE_LAYOUT_TYPES,
    TWO_BUTTON_LAYOUT_GENERIC,
    TWO_BUTTON_LAYOUT_RIGHT_PAIR,
    TWO_BUTTON_LAYOUTS,
    TWO_BUTTON_PLATE_KEYS,
)
from .protocol import is_motion_detector, motion_detector_bus_addresses
from ..const import DEVICE_ADDRESS_INVENTORY, DEVICE_INVENTORY_ANSWER

__all__ = [
    "NikobusDiscovery",
    "DecodedCommand",
    "Decoder",
    "DiscoveryProgress",
    "InventoryQueryType",
    "InventoryResult",
    "PHASE_FINALIZING",
    "PHASE_IDENTITY",
    "PHASE_INVENTORY",
    "PHASE_REGISTER_SCAN",
    "DEVICE_ADDRESS_INVENTORY",
    "DEVICE_INVENTORY_ANSWER",
    "IR_OP_POINT_PREFIX",
    "find_ir_operation_point",
    "find_module",
    "find_operation_point",
    # What a host needs for key layouts and the motion detector (0.57.3):
    # the integration imports these from here, not from module internals.
    "NON_OUTPUT_MODULE_TYPES",
    "DEVICE_TYPE_KEY_MAPPING",
    "MOTION_DETECTOR_KEY_ROLES",
    "PER_PLATE_LAYOUT_TYPES",
    "TWO_BUTTON_LAYOUT_GENERIC",
    "TWO_BUTTON_LAYOUT_RIGHT_PAIR",
    "TWO_BUTTON_LAYOUTS",
    "TWO_BUTTON_PLATE_KEYS",
    "has_per_plate_key_layout",
    "two_button_bus_addresses",
    "is_motion_detector",
    "motion_detector_bus_addresses",
]
