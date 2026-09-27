"""A simulated Nikobus installation, speaking the PC-Link protocol."""

from .module import SimulatedModule
from .server import NikobusSimulator
from .topology import Installation, Link, ModuleSpec, load_installation, preset

__all__ = [
    "Installation",
    "Link",
    "ModuleSpec",
    "NikobusSimulator",
    "SimulatedModule",
    "load_installation",
    "preset",
]
