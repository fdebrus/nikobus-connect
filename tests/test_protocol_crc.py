"""Tests for custom_components.nikobus.nkbprotocol — pure protocol utilities."""

import unittest

from nikobus_connect.protocol import (
    _reverse_bits,
    append_crc1,
    append_crc2,
    calc_crc1,
    calc_crc2,
    calculate_group_number,
    int_to_hex,
    make_pc_link_command,
    nikobus_button_to_module,
    nikobus_to_button_address,
    reverse_24bit_to_hex,
)


# ---------------------------------------------------------------------------
# int_to_hex
# ---------------------------------------------------------------------------

class TestIntToHex(unittest.TestCase):
    def test_zero_two_digits(self):
        self.assertEqual(int_to_hex(0, 2), "00")

    def test_max_byte(self):
        self.assertEqual(int_to_hex(255, 2), "FF")

    def test_four_digit_padding(self):
        self.assertEqual(int_to_hex(1, 4), "0001")

    def test_four_digit_value(self):
        self.assertEqual(int_to_hex(0xABCD, 4), "ABCD")

    def test_uppercase_output(self):
        self.assertEqual(int_to_hex(0xAB, 2), "AB")

    def test_overflow_uses_more_digits(self):
        # Value wider than `digits` should still render completely
        self.assertEqual(int_to_hex(256, 2), "100")


# ---------------------------------------------------------------------------
# calc_crc1  (CRC-16/ANSI X3.28)
# ---------------------------------------------------------------------------

class TestCalcCrc1(unittest.TestCase):
    def test_returns_int_in_range(self):
        result = calc_crc1("1500C1C7")
        self.assertIsInstance(result, int)
        self.assertGreaterEqual(result, 0)
        self.assertLessEqual(result, 0xFFFF)

    def test_deterministic(self):
        data = "12C7C1AABB"
        self.assertEqual(calc_crc1(data), calc_crc1(data))

    def test_different_data_different_crc(self):
        self.assertNotEqual(calc_crc1("1500C1C7"), calc_crc1("1700C1C7"))

    def test_invalid_hex_raises(self):
        with self.assertRaises(Exception):
            calc_crc1("GGGG")


# ---------------------------------------------------------------------------
# calc_crc2  (CRC-8/ATM)
# ---------------------------------------------------------------------------

class TestCalcCrc2(unittest.TestCase):
    def test_returns_byte(self):
        result = calc_crc2("$1CC7C1")
        self.assertGreaterEqual(result, 0)
        self.assertLessEqual(result, 0xFF)

    def test_deterministic(self):
        s = "$1CC7C10000112233"
        self.assertEqual(calc_crc2(s), calc_crc2(s))

    def test_different_inputs_differ(self):
        self.assertNotEqual(calc_crc2("ABC"), calc_crc2("ABD"))

    def test_empty_string_returns_zero(self):
        self.assertEqual(calc_crc2(""), 0)


# ---------------------------------------------------------------------------
# append_crc1 / append_crc2
# ---------------------------------------------------------------------------

class TestAppendCrc(unittest.TestCase):
    def test_append_crc1_length(self):
        data = "12C7C1"
        result = append_crc1(data)
        self.assertEqual(len(result), len(data) + 4)

    def test_append_crc1_prefix(self):
        data = "12C7C1"
        self.assertTrue(append_crc1(data).startswith(data))

    def test_append_crc2_length(self):
        s = "$1412C7C1ABCD"
        result = append_crc2(s)
        self.assertEqual(len(result), len(s) + 2)

    def test_append_crc2_prefix(self):
        s = "$14AABB"
        self.assertTrue(append_crc2(s).startswith(s))


# ---------------------------------------------------------------------------
# make_pc_link_command
# ---------------------------------------------------------------------------

class TestMakePcLinkCommand(unittest.TestCase):
    """make_pc_link_command produces a CRC-valid bus frame."""

    def _cmd(self, func=0x12, addr="C1C7", args=None):
        return make_pc_link_command(func, addr, args)

    def test_starts_with_dollar(self):
        self.assertTrue(self._cmd().startswith("$"))

    def test_address_little_endian_in_frame(self):
        # Address C1C7 in little-endian is C7C1
        cmd = make_pc_link_command(0x12, "C1C7")
        self.assertIn("C7C1", cmd)

    def test_func_code_in_frame(self):
        # Group-1 GET uses func 0x12
        cmd = make_pc_link_command(0x12, "C1C7")
        self.assertIn("12", cmd)

    def test_group2_func_code(self):
        cmd = make_pc_link_command(0x17, "C1C7")
        self.assertIn("17", cmd)

    def test_set_command_with_args(self):
        args = bytearray([0xFF, 0x00, 0xAA, 0xBB, 0xCC, 0xDD, 0xFF])
        cmd = make_pc_link_command(0x15, "C1C7", args)
        self.assertTrue(cmd.startswith("$"))

    def test_frame_passes_validate_crc(self):
        """The frame produced must pass the same CRC logic used by the listener."""
        from nikobus_connect.listener import NikobusEventListener
        # validate_crc is a plain method; create a minimal listener
        from unittest.mock import MagicMock
        listener = NikobusEventListener.__new__(NikobusEventListener)
        listener._frame_buffer = ""

        for func, addr in [(0x12, "C1C7"), (0x17, "C1C7"), (0x15, "AABB"), (0x16, "1234")]:
            cmd = make_pc_link_command(func, addr)
            self.assertTrue(
                listener.validate_crc(cmd),
                f"validate_crc failed for func={func:#04x} addr={addr} cmd={cmd}",
            )

    def test_length_field_consistent(self):
        cmd = make_pc_link_command(0x12, "C1C7")
        length_field = int(cmd[1:3], 16)
        # Length field = len(frame) + 1
        self.assertEqual(len(cmd), length_field - 1)


# ---------------------------------------------------------------------------
# calculate_group_number
# ---------------------------------------------------------------------------

class TestCalculateGroupNumber(unittest.TestCase):
    def test_channels_1_to_6_are_group_1(self):
        for ch in range(1, 7):
            with self.subTest(channel=ch):
                self.assertEqual(calculate_group_number(ch), 1)

    def test_channels_7_to_12_are_group_2(self):
        for ch in range(7, 13):
            with self.subTest(channel=ch):
                self.assertEqual(calculate_group_number(ch), 2)

    def test_boundary_channel_6(self):
        self.assertEqual(calculate_group_number(6), 1)

    def test_boundary_channel_7(self):
        self.assertEqual(calculate_group_number(7), 2)


# ---------------------------------------------------------------------------
# _reverse_bits
# ---------------------------------------------------------------------------

class TestReverseBits(unittest.TestCase):
    def test_single_bit_1(self):
        self.assertEqual(_reverse_bits(1, 1), 1)

    def test_single_bit_0(self):
        self.assertEqual(_reverse_bits(0, 1), 0)

    def test_8bit_msb_becomes_lsb(self):
        # 0b10000000 reversed in 8 bits → 0b00000001
        self.assertEqual(_reverse_bits(0b10000000, 8), 0b00000001)

    def test_8bit_pattern(self):
        # 0b10110100 → 0b00101101
        self.assertEqual(_reverse_bits(0b10110100, 8), 0b00101101)

    def test_4bit_alternating(self):
        # 0b1010 reversed in 4 bits → 0b0101
        self.assertEqual(_reverse_bits(0b1010, 4), 0b0101)

    def test_identity_palindrome(self):
        # 0b00000000 reversed = 0
        self.assertEqual(_reverse_bits(0, 8), 0)


# ---------------------------------------------------------------------------
# reverse_24bit_to_hex
# ---------------------------------------------------------------------------

class TestReverse24BitToHex(unittest.TestCase):
    def test_returns_6_char_hex(self):
        result = reverse_24bit_to_hex(0x123456)
        self.assertEqual(len(result), 6)

    def test_output_uppercase(self):
        result = reverse_24bit_to_hex(0xABCDEF)
        self.assertEqual(result, result.upper())

    def test_zero_is_all_zeros(self):
        self.assertEqual(reverse_24bit_to_hex(0), "000000")

    def test_all_ones_remain_all_ones(self):
        # 0xFFFFFF bit-reversed is still 0xFFFFFF
        self.assertEqual(reverse_24bit_to_hex(0xFFFFFF), "FFFFFF")

    def test_known_reversal(self):
        # 0x800000 = bit 23 set → reversed → bit 0 set = 0x000001
        self.assertEqual(reverse_24bit_to_hex(0x800000), "000001")


# ---------------------------------------------------------------------------
# nikobus_to_button_address / nikobus_button_to_module  (roundtrip)
# ---------------------------------------------------------------------------

class TestButtonAddressRoundtrip(unittest.TestCase):
    """The vendor's rule: ``bit_reverse_24(plate << 2 | key_code)``.

    A plate address is 22 bits wide (``plate << 2`` must fit in 24), and
    the code a key label carries depends on the plate's key count
    (``KEY_MAPPING``): 1C is code 0 on a four-key plate and code 4 on an
    eight-key one. Pinned by the validating install of Nikobus-HA #519:
    plate ``124A36``, key 1C presses ``#N1B1492``, key 1D ``#N5B1492``.
    """

    FOUR = ["1A", "1B", "1C", "1D"]
    EIGHT = ["1A", "1B", "1C", "1D", "2A", "2B", "2C", "2D"]

    def test_validated_install(self):
        self.assertEqual(nikobus_to_button_address("124A36", "1C", 4), "#N1B1492")
        self.assertEqual(nikobus_to_button_address("124A36", "1D", 4), "#N5B1492")
        self.assertEqual(nikobus_button_to_module("#N1B1492", 4), ("124A36", "1C"))
        self.assertEqual(nikobus_button_to_module("#N5B1492", 4), ("124A36", "1D"))

    def test_agrees_with_the_project_file_parser(self):
        from nikobus_connect.nkb.parser import per_key_bus_address

        for channels, labels in ((4, self.FOUR), (8, self.EIGHT)):
            for btn in labels:
                with self.subTest(channels=channels, button=btn):
                    frame = nikobus_to_button_address("124A36", btn, channels)
                    self.assertEqual(frame[2:], per_key_bus_address("124A36", channels, btn))

    def test_roundtrip_four_and_eight_key_plates(self):
        for plate in ("1A2B3C", "3BCDE0", "000000", "3FFFFE"):
            for channels, labels in ((4, self.FOUR), (8, self.EIGHT)):
                for btn in labels:
                    with self.subTest(plate=plate, channels=channels, button=btn):
                        frame = nikobus_to_button_address(plate, btn, channels)
                        self.assertEqual(nikobus_button_to_module(frame, channels), (plate, btn))

    def test_default_key_count_follows_the_label(self):
        # A ``2x`` label can only be on an eight-key plate; ``1x`` defaults to four.
        self.assertEqual(nikobus_to_button_address("124A36", "2C"), "#N1B1492")
        self.assertEqual(nikobus_to_button_address("124A36", "1C"), "#N1B1492")

    def test_different_buttons_give_different_frames(self):
        frames = {btn: nikobus_to_button_address("1A2B3C", btn, 8) for btn in self.EIGHT}
        self.assertEqual(len(set(frames.values())), len(self.EIGHT))

    def test_invalid_button_raises_value_error(self):
        with self.assertRaises(ValueError):
            nikobus_to_button_address("1A2B3C", "9X")
        with self.assertRaises(ValueError):
            nikobus_to_button_address("1A2B3C", "2A", 4)

    def test_invalid_frame_too_short_raises(self):
        with self.assertRaises(ValueError):
            nikobus_button_to_module("TOOSHORT")

    def test_invalid_frame_wrong_prefix_raises(self):
        with self.assertRaises(ValueError):
            nikobus_button_to_module("$N123456")


if __name__ == "__main__":
    unittest.main()
