"""Feedback module (05-207) image decoder and reader."""

from __future__ import annotations

import asyncio

from nikobus_connect.api import MODULE_CRC_UNKNOWN, MODULE_IMAGE_SIZES, NikobusAPI
from nikobus_connect.discovery.feedback_decoder import (
    FEEDBACK_IMAGE_SIZE,
    LED_MODE_TABLE_OFFSET,
    REGION_GROUP_ADDRESSES,
    REGION_LED_LISTS,
    REGION_OUTPUT_MODULES,
    decode_feedback_image,
    key_address_from_input,
    key_index_from_input,
    plate_addresses_for_group,
    reverse_bits24,
)
from nikobus_connect.protocol import FUNC_READ_BLOCK16, make_block_index_args


def _image() -> bytearray:
    """One 4-key plate (group 0, module address 0x610EC) and the module's
    own LED 1, tracking switch module 5B05 channels 1 and 6 and dimmer
    0E6C channel 2."""
    img = bytearray(b"\xff" * FEEDBACK_IMAGE_SIZE)
    base = REGION_OUTPUT_MODULES[0]
    img[base : base + 8] = bytes([1, 0x5B, 0x05, 0, 0x00, 0x21, 0xFF, 0xFF])
    img[base + 8 : base + 16] = bytes([3, 0x0E, 0x6C, 0, 0x00, 0x02, 0xFF, 0xFF])
    grp = REGION_GROUP_ADDRESSES[0]
    img[grp : grp + 3] = bytes([0x06, 0x10, 0xEC])
    lists = REGION_LED_LISTS[0]
    stream = bytes(
        [
            0x00, 0x00, 0x04, 0x00,               # slot 0 -> output 0, normal polarity
            0x01, 0x01, 0x05, 0x01,               # slot 1 -> output 1 inverted, inverted polarity
            0x02, 0x02, 0x61, 0x0E, 0xD1, 0x04, 0x03,  # slot 3 -> output 2 with input 610ED1 (key A of 1843B4)
            0x00, 0x02, 0x04, 0xC0,               # slot 192 (own LED 1) -> output 2
            0x08, 0xFF, 0x05, 0x02,               # list-less slot 2, mode off
        ]
    )
    img[lists : lists + len(stream)] = stream
    img[LED_MODE_TABLE_OFFSET + 0] = 0
    img[LED_MODE_TABLE_OFFSET + 1] = 1
    img[LED_MODE_TABLE_OFFSET + 2] = 4
    img[LED_MODE_TABLE_OFFSET + 3] = 2
    # input record: key A of plate 1843B4 (input 0x610ED1), M01, drives output 0 (switch)
    addr = 0x610ED1
    p3 = 7
    img[0:8] = bytes(
        [
            (addr >> 16) & 0xFF,
            (addr >> 8) & 0xFF,
            (addr & 0xFC) | ((p3 >> 2) & 3),
            (1 << 4) | 0,
            ((p3 & 3) << 6) | ((addr & 3) << 4) | 1,
            0,
            0,
            0,
        ]
    )
    return img


def test_address_helpers_match_real_plate():
    # Plate 1843B4, key A transmits 8B7086: bit-reversed = 0x610ED1.
    assert reverse_bits24(0x8B7086) == 0x610ED1
    assert key_address_from_input(0x610ED1) == "8B7086"
    assert key_index_from_input(0x610ED1) == 1
    # The group table clears bit 0 for 4-LED plates: both variants offered.
    assert plate_addresses_for_group(0x610EC) == ("1843B0", "1843B4")
    assert plate_addresses_for_group(0x610ED) == ("1843B4", "1843B0")


def test_decode_outputs_and_leds():
    decoded = decode_feedback_image(bytes(_image()))
    assert [m["module_address"] for m in decoded.modules] == ["5B05", "0E6C"]
    assert [(o.module_address, o.channel) for o in decoded.outputs] == [
        ("5B05", 1),
        ("5B05", 6),
        ("0E6C", 2),
    ]
    assert decoded.outputs[2].module_type == "dimmer_module"
    assert decoded.group_addresses[0] == 0x610EC
    assert decoded.group_addresses[1] is None

    leds = {led.slot: led for led in decoded.leds}
    assert set(leds) == {0, 1, 2, 3, 192}
    assert leds[0].plate_addresses == ("1843B0", "1843B4")
    assert (leds[0].group, leds[0].row, leds[0].mode_name) == (0, 0, "auto")
    assert [(o.output.module_address, o.output.channel) for o in leds[0].outputs] == [("5B05", 1)]
    assert leds[1].outputs[0].inverted is True
    assert leds[1].polarity_inverted is True
    assert leds[1].mode_name == "auto_inverted"
    assert leds[2].listless is True and leds[2].outputs == [] and leds[2].mode_name == "off"
    assert leds[3].outputs[0].input_address == "8B7086"
    assert leds[3].outputs[0].output.channel == 2
    assert leds[192].own_led == 1 and leds[192].plate_addresses == ()

    assert len(decoded.input_records) == 1
    rec = decoded.input_records[0]
    assert rec.key_address == "8B7086"
    assert (rec.link_mode, rec.param1, rec.param3) == (0, 1, 7)
    assert rec.output.module_address == "5B05" and rec.output.channel == 1

    as_dict = decoded.as_dict()
    assert as_dict["leds"][0]["outputs"][0]["module_address"] == "5B05"
    assert as_dict["group_addresses"][0] == "0610EC"


def test_partial_image_is_padded():
    decoded = decode_feedback_image(bytes(_image())[: REGION_LED_LISTS[0] + 32])
    assert decoded.modules == []  # region 0x6000 missing -> padded FF
    assert decoded.leds and decoded.leds[0].outputs[0].output is None


class _FakeHandler:
    """Answers block reads from an in-memory image and records the requests."""

    def __init__(self, image: bytes) -> None:
        self.image = image
        self.blocks: list[int] = []

    async def query(self, func: int, address: str, args: bytes | None = None) -> bytes:
        assert func == FUNC_READ_BLOCK16
        assert args is not None
        block = int.from_bytes(args, "little")
        self.blocks.append(block)
        start = block * 16
        return bytes.fromhex("6C96") + self.image[start : start + 16]


def test_feedback_image_read_is_bounded():
    original = bytes(_image())
    handler = _FakeHandler(original)
    api = NikobusAPI(handler, {})
    progress: list[tuple[int, int]] = []

    image = asyncio.run(
        api.read_module_memory("966C", "feedback_module", lambda d, t: progress.append((d, t)))
    )

    assert len(image) == FEEDBACK_IMAGE_SIZE
    assert decode_feedback_image(image).as_dict() == decode_feedback_image(original).as_dict()
    # Fixed tables in full, FF-terminated ones up to the first empty block.
    assert handler.blocks[:16] == list(range(0x600, 0x610))
    assert handler.blocks[16:32] == list(range(0x610, 0x620))
    assert handler.blocks[32:44] == list(range(0x780, 0x78C))
    led_blocks = [b for b in handler.blocks if 0x400 <= b < 0x600]
    assert led_blocks == [0x400, 0x401, 0x402]  # 23-byte stream, then the first empty block
    record_blocks = [b for b in handler.blocks if b < 0x400]
    assert record_blocks == [0, 1]  # one record + terminator, then an empty block
    assert progress[-1][0] == len(handler.blocks)


def test_feedback_module_registered_for_backup_but_not_crc():
    assert MODULE_IMAGE_SIZES["feedback_module"] == FEEDBACK_IMAGE_SIZE
    assert "feedback_module" in MODULE_CRC_UNKNOWN
    assert make_block_index_args(0x600) == b"\x00\x06"


class _LinkModeHandler(_FakeHandler):
    """Ignores block reads until link mode is on, like a real 05-207."""

    def __init__(self, image: bytes, *, fail_in_link_mode: bool = False) -> None:
        super().__init__(image)
        self.link_mode = False
        self.fail_in_link_mode = fail_in_link_mode
        self.funcs: list[int] = []

    async def query(self, func: int, address: str, args: bytes | None = None) -> bytes:
        from nikobus_connect.exceptions import NikobusTimeoutError
        from nikobus_connect.protocol import FUNC_LINK_MODE_OFF, FUNC_LINK_MODE_ON

        self.funcs.append(func)
        if func == FUNC_LINK_MODE_ON:
            self.link_mode = True
            return b""
        if func == FUNC_LINK_MODE_OFF:
            self.link_mode = False
            return b""
        if not self.link_mode or self.fail_in_link_mode:
            raise NikobusTimeoutError("no ack")
        return await super().query(func, address, args)


def test_feedback_read_retries_in_link_mode_and_leaves_it():
    from nikobus_connect.protocol import FUNC_LINK_MODE_OFF, FUNC_LINK_MODE_ON

    original = bytes(_image())
    handler = _LinkModeHandler(original)
    api = NikobusAPI(handler, {})
    image = asyncio.run(api.read_module_memory("966C", "feedback_module"))
    assert decode_feedback_image(image).as_dict() == decode_feedback_image(original).as_dict()
    # one ignored read, link mode on, the reads, link mode off
    assert handler.funcs[0] == FUNC_READ_BLOCK16
    assert handler.funcs[1] == FUNC_LINK_MODE_ON
    assert handler.funcs[-1] == FUNC_LINK_MODE_OFF
    assert handler.link_mode is False


def test_feedback_read_leaves_link_mode_on_failure():
    import pytest

    from nikobus_connect.exceptions import NikobusTimeoutError
    from nikobus_connect.protocol import FUNC_LINK_MODE_OFF

    handler = _LinkModeHandler(bytes(_image()), fail_in_link_mode=True)
    api = NikobusAPI(handler, {})
    with pytest.raises(NikobusTimeoutError):
        asyncio.run(api.read_feedback_image("966C"))
    assert handler.funcs[-1] == FUNC_LINK_MODE_OFF
    assert handler.link_mode is False


def test_feedback_read_without_link_mode_fallback_raises():
    import pytest

    from nikobus_connect.exceptions import NikobusTimeoutError

    handler = _LinkModeHandler(bytes(_image()))
    api = NikobusAPI(handler, {})
    with pytest.raises(NikobusTimeoutError):
        asyncio.run(api.read_feedback_image("966C", link_mode=False))
    assert handler.funcs == [FUNC_READ_BLOCK16]
