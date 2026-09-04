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


def _no_sleep(monkeypatch):
    import nikobus_connect.api as api_module

    async def _instant(_seconds):
        return None

    monkeypatch.setattr(api_module.asyncio, "sleep", _instant)


class _FakeHandler:
    """Answers block reads from an in-memory image and records the requests."""

    def __init__(self, image: bytes) -> None:
        self.image = image
        self.blocks: list[int] = []

    async def query(self, func: int, address: str, args: bytes | None = None) -> bytes:
        if func in (0x18, 0x19):  # link mode on / off: acknowledged only
            return b""
        assert func == FUNC_READ_BLOCK16
        assert args is not None
        block = int.from_bytes(args, "little")
        self.blocks.append(block)
        start = block * 16
        return bytes.fromhex("6C96") + self.image[start : start + 16]


def test_feedback_image_read_is_bounded(monkeypatch):
    _no_sleep(monkeypatch)
    original = bytes(_image())
    handler = _FakeHandler(original)
    api = NikobusAPI(handler, {})
    progress: list[tuple[int, int]] = []

    image = asyncio.run(
        api.read_module_memory("966C", "feedback_module", lambda d, t: progress.append((d, t)))
    )

    assert len(image) == FEEDBACK_IMAGE_SIZE
    assert decode_feedback_image(image).as_dict() == decode_feedback_image(original).as_dict()
    # Fixed tables in full, FF-terminated ones up to the first empty block,
    # most important regions first.
    assert handler.blocks[:16] == list(range(0x600, 0x610))
    assert handler.blocks[16:32] == list(range(0x610, 0x620))
    assert handler.blocks[32:35] == [0x400, 0x401, 0x402]  # 23-byte stream, then the first empty block
    assert handler.blocks[35:47] == list(range(0x780, 0x78C))
    assert handler.blocks[47:] == [0, 1]  # one record + terminator, then an empty block
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


def test_feedback_read_uses_link_mode_and_leaves_it(monkeypatch):
    from nikobus_connect.protocol import FUNC_LINK_MODE_OFF, FUNC_LINK_MODE_ON

    _no_sleep(monkeypatch)
    original = bytes(_image())
    handler = _LinkModeHandler(original)
    api = NikobusAPI(handler, {})
    image = asyncio.run(api.read_module_memory("966C", "feedback_module"))
    assert decode_feedback_image(image).as_dict() == decode_feedback_image(original).as_dict()
    # link mode on, the reads, link mode off
    assert handler.funcs[0] == FUNC_LINK_MODE_ON
    assert handler.funcs[1] == FUNC_READ_BLOCK16
    assert handler.funcs[-1] == FUNC_LINK_MODE_OFF
    assert handler.link_mode is False


class _FlakyHandler(_LinkModeHandler):
    """Drops the first attempt of every block and never serves the mode table."""

    def __init__(self, image: bytes) -> None:
        super().__init__(image)
        self.seen: dict[int, int] = {}

    async def query(self, func: int, address: str, args: bytes | None = None) -> bytes:
        from nikobus_connect.exceptions import NikobusTimeoutError

        if func == FUNC_READ_BLOCK16 and args is not None:
            block = int.from_bytes(args, "little")
            self.seen[block] = self.seen.get(block, 0) + 1
            if 0x780 <= block < 0x78C or self.seen[block] == 1:
                self.funcs.append(func)
                raise NikobusTimeoutError("no ack")
        return await super().query(func, address, args)


def test_feedback_read_retries_blocks_and_tolerates_missing_modes(monkeypatch):
    from nikobus_connect.protocol import FUNC_LINK_MODE_OFF

    _no_sleep(monkeypatch)
    original = bytes(_image())
    handler = _FlakyHandler(original)
    api = NikobusAPI(handler, {})
    image = asyncio.run(api.read_feedback_image("966C"))
    decoded = decode_feedback_image(image)
    expected = decode_feedback_image(original)
    assert [led.slot for led in decoded.leds] == [led.slot for led in expected.leds]
    assert decoded.leds[0].mode is None  # mode table unreadable -> unknown, not an error
    assert decoded.outputs[0].module_address == "5B05"
    assert handler.funcs[-1] == FUNC_LINK_MODE_OFF
    assert handler.link_mode is False


def test_feedback_read_leaves_link_mode_on_failure(monkeypatch):
    import pytest

    from nikobus_connect.exceptions import NikobusTimeoutError
    from nikobus_connect.protocol import FUNC_LINK_MODE_OFF

    _no_sleep(monkeypatch)
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
    assert handler.funcs == [FUNC_READ_BLOCK16] * 3  # retries, never link mode


def test_live_image_layout_from_real_module():
    """Bytes read from a real 05-207 (966C): group table at region offset
    0x60, tracked-output records with a running first-output index, one
    LED (slot 2 = key C) tracking C9A5 channel 9, four input records."""
    from nikobus_connect.discovery.feedback_decoder import (
        KEY_LABELS_BY_ROW,
        group_table_base,
    )

    img = bytearray(b"\xff" * FEEDBACK_IMAGE_SIZE)
    img[0:32] = bytes.fromhex(
        "3085985511000000308E1C5511000000" "4054ACF4010000005F07303511000000"
    )
    img[0x4000:0x4004] = bytes.fromhex("00000402")
    img[0x6000:0x6030] = bytes.fromhex(
        "030E6C000000FFFF" "029105000000FFFF" "028394000000FFFF"
        "01C9A5000100FFFF" "095B05010000FFFF" "014707010000FFFF"
    )
    img[0x6160:0x6163] = bytes.fromhex("10152B")
    img[0x6170] = 0x00

    assert group_table_base(bytes(img)) == 0x60
    decoded = decode_feedback_image(bytes(img))
    assert decoded.group_table_base == 0x60
    assert [m["first_output_index"] for m in decoded.modules] == [0, 0, 0, 0, 1, 1]
    assert len(decoded.outputs) == 1
    assert (decoded.outputs[0].module_address, decoded.outputs[0].channel) == ("C9A5", 9)
    assert decoded.group_addresses[0] == 0x10152B
    assert decoded.group_addresses[1] is None

    (led,) = decoded.leds
    assert (led.slot, led.group, led.row) == (2, 0, 2)
    assert led.plate_addresses[0] == "4054AC"
    assert KEY_LABELS_BY_ROW[4][led.row] == "1C"
    assert led.outputs[0].output.module_address == "C9A5"
    assert led.mode is None  # this build writes no mode table

    keys = [rec.key_address for rec in decoded.input_records]
    assert keys == ["99A10C", "B8710C", "352A02", "8CE0FA"]
    assert all(rec.output is decoded.outputs[0] for rec in decoded.input_records)
    # key C of plate 4054AC is index 0: 352A02 bit-reversed = 4054AC | 0
    assert key_index_from_input(reverse_bits24(0x352A02)) == 0


def test_first_output_index_creates_gaps_and_running_fallback():
    from nikobus_connect.discovery.feedback_decoder import output_table

    modules = [
        {"eeprom_type": 1, "module_type": "switch_module", "module_address": "AAAA", "first_output_index": 0, "channel_mask": 0b11},
        {"eeprom_type": 2, "module_type": "roller_module", "module_address": "BBBB", "first_output_index": 4, "channel_mask": 0b1},
        {"eeprom_type": 3, "module_type": "dimmer_module", "module_address": "CCCC", "first_output_index": 0, "channel_mask": 0b1},
    ]
    table = output_table(modules)
    assert [(o.module_address, o.channel) if o else None for o in table] == [
        ("AAAA", 1), ("AAAA", 2), None, None, ("BBBB", 1), ("CCCC", 1)
    ]


class _QuietingHandler(_LinkModeHandler):
    """Goes silent after N answered blocks until link mode is re-entered."""

    def __init__(self, image: bytes, answers_per_session: int) -> None:
        super().__init__(image)
        self.answers_per_session = answers_per_session
        self.answered = 0
        self.sessions = 0

    async def query(self, func: int, address: str, args: bytes | None = None) -> bytes:
        from nikobus_connect.exceptions import NikobusTimeoutError
        from nikobus_connect.protocol import FUNC_LINK_MODE_ON

        if func == FUNC_LINK_MODE_ON:
            self.sessions += 1
            self.answered = 0
        if func == FUNC_READ_BLOCK16:
            if self.answered >= self.answers_per_session:
                self.funcs.append(func)
                raise NikobusTimeoutError("quiet")
            self.answered += 1
        return await super().query(func, address, args)


def test_feedback_read_reenters_link_mode_when_module_goes_quiet(monkeypatch):
    from nikobus_connect.protocol import FUNC_LINK_MODE_OFF, FUNC_LINK_MODE_ON

    _no_sleep(monkeypatch)
    original = bytes(_image())
    handler = _QuietingHandler(original, answers_per_session=20)
    api = NikobusAPI(handler, {})
    image = asyncio.run(api.read_feedback_image("966C"))
    assert decode_feedback_image(image).as_dict() == decode_feedback_image(original).as_dict()
    assert handler.sessions >= 2  # at least one refresh happened
    assert handler.funcs.count(FUNC_LINK_MODE_ON) == handler.funcs.count(FUNC_LINK_MODE_OFF)
    assert handler.link_mode is False


def test_feedback_read_gives_up_optional_regions_after_refreshes(monkeypatch):
    _no_sleep(monkeypatch)
    original = bytes(_image())
    # 35 answers: output table (16) + plate table (16) + LED list (3), then quiet.
    handler = _QuietingHandler(original, answers_per_session=35)
    api = NikobusAPI(handler, {})

    # After the refreshes are spent the module keeps quiet: make every
    # session after the first answer nothing.
    original_query = handler.query

    async def query(func, address, args=None):
        if handler.sessions >= 2 and func == FUNC_READ_BLOCK16:
            from nikobus_connect.exceptions import NikobusTimeoutError

            raise NikobusTimeoutError("quiet")
        return await original_query(func, address, args)

    handler.query = query  # type: ignore[method-assign]
    image = asyncio.run(api.read_feedback_image("966C"))
    decoded = decode_feedback_image(image)
    assert [led.slot for led in decoded.leds] == [0, 1, 2, 3, 192]  # LED map complete
    assert decoded.leds[0].mode is None  # modes not read
    assert decoded.input_records == []  # input records not read
    assert handler.link_mode is False
