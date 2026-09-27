"""Where the fixtures of a real module live.

The simulator is only worth anything if its output matches hardware, so
the tests read the library's own captures rather than keeping copies.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

#: The library checkout the simulator sits in.
LIBRARY_ROOT = Path(__file__).resolve().parents[2]
AUDIO_FIXTURE = LIBRARY_ROOT / "tests" / "fixtures" / "audio_module_8334_bank01.json"


@pytest.fixture(scope="session")
def audio_8334_registers() -> dict[str, str]:
    """Bank 01 of a real 05-205 with four zones (module 8334)."""
    return json.loads(AUDIO_FIXTURE.read_text())
