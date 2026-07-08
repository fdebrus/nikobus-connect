"""Nikobus ``.nkb`` project-file reader.

A ``.nkb`` is the export from Niko's PC software — a ZIP holding an MS
Access database with the install's user-given names, rooms, per-output
names, and Central Function (scene) groups. The bus itself carries none
of this (only wiring), so a consumer that wants friendly names / Areas /
named scenes has to read them from the project file.

``parse_nkb`` is the entry point; it returns an :class:`NkbData`. The MS
Access reader is vendored under ``_access_parser`` (the upstream sdist
won't build on modern setuptools), so the only runtime dependency is
``construct``.
"""

from __future__ import annotations

from .config_builder import NkbConfig, build_config, build_config_from_nkb
from .parser import (
    CANONICAL_NKB_FILENAME,
    NkbData,
    SceneDef,
    find_nkb_file,
    mode_code,
    parse_nkb,
)

__all__ = [
    "CANONICAL_NKB_FILENAME",
    "NkbConfig",
    "NkbData",
    "SceneDef",
    "build_config",
    "build_config_from_nkb",
    "find_nkb_file",
    "mode_code",
    "parse_nkb",
]
