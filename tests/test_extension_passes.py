"""The scan loop reads the blocks a decoder asks for after its planned band.

A decoder whose table carries its own count (the audio table, the
PC-Logic input table) exposes ``extension_passes()``; the loop runs
those sections after the plan, tells the decoder which block each read
answers, and stops when the decoder has nothing more to ask.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nikobus_connect.discovery.discovery import NikobusDiscovery


def _drop_coro(coro):
    try:
        coro.close()
    except AttributeError:
        pass
    task = MagicMock()
    task.cancel = MagicMock()
    return task


def _make_coordinator() -> MagicMock:
    coord = MagicMock()
    coord.dict_module_data = {}
    coord.discovery_running = False
    coord.discovery_module = True
    coord.discovery_module_address = None
    coord.inventory_query_type = None
    coord.get_module_channel_count = MagicMock(return_value=0)
    return coord


class _ExtendingDecoder:
    module_type = "pc_logic"

    def __init__(self, extensions: list[tuple]) -> None:
        self._extensions = list(extensions)
        self.calls = 0

    def can_handle(self, module_type: str) -> bool:
        return module_type == self.module_type

    def reset_scan_buffers(self) -> None:
        pass

    def extension_passes(self) -> tuple:
        self.calls += 1
        return self._extensions.pop(0) if self._extensions else ()


class _PlainDecoder(_ExtendingDecoder):
    extension_passes = None  # type: ignore[assignment]


async def _run(tmp_path, decoder) -> tuple[list[dict], NikobusDiscovery]:
    coord = _make_coordinator()
    discovery = NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )
    discovery.discovered_devices = {
        "940C": {"address": "940C", "category": "Module", "model": "05-201", "device_type": "08"}
    }
    discovery._is_known_module_address = MagicMock(return_value=True)
    discovery._resolve_module_type = MagicMock(return_value="pc_logic")
    discovery._decoders = [decoder]

    scan_calls: list[dict] = []

    async def fake_scan(address, base_cmd, command_range, sub_byte="04"):
        scan_calls.append({"sub_byte": sub_byte, "registers": tuple(command_range)})

    discovery._scan_module_registers = fake_scan
    discovery._finalize_discovery = AsyncMock()
    await discovery.query_module_inventory("940C")
    return scan_calls, discovery


@pytest.mark.asyncio
async def test_extension_sections_run_after_the_plan(tmp_path):
    decoder = _ExtendingDecoder([(("00", (0x40, 0x41)), ("02", (0x00,)))])
    scan_calls, discovery = await _run(tmp_path, decoder)
    assert len(scan_calls) >= 5
    assert scan_calls[-2:] == [
        {"sub_byte": "00", "registers": (0x40, 0x41)},
        {"sub_byte": "02", "registers": (0x00,)},
    ]
    # Asked once more after the extension, answered "nothing".
    assert decoder.calls == 2
    assert discovery._finalize_discovery.await_count == 1


@pytest.mark.asyncio
async def test_a_decoder_without_the_hook_changes_nothing(tmp_path):
    scan_calls, _ = await _run(tmp_path, _PlainDecoder([]))
    assert {c["sub_byte"] for c in scan_calls} == {"00", "02", "03"}


@pytest.mark.asyncio
async def test_extension_rounds_are_bounded(tmp_path):
    endless = [(("00", (0x40,)),)] * 50
    decoder = _ExtendingDecoder(endless)
    scan_calls, _ = await _run(tmp_path, decoder)
    extra = [c for c in scan_calls if c["registers"] == (0x40,)]
    assert 0 < len(extra) <= 4
