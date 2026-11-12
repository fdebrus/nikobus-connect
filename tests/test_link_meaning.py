"""``describe_link``: a link's mode and its T1 / T2 said in words.

The labels go in as the real decoders write them: each record here is
decoded by the module's own decoder and its ``M`` / ``T1`` / ``T2``
handed on, so a label the tables change is caught here.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

import pytest

from nikobus_connect.discovery import (
    LinkMeaning,
    describe_link,
    dimmer_decoder,
    key_position,
    shutter_decoder,
    switch_decoder,
)
from nikobus_connect.discovery.mapping import (
    DIMMER_MODE_MAPPING,
    ROLLER_MODE_MAPPING,
    SWITCH_MODE_MAPPING,
)


class _Context:
    """What a decoder reads of its context; no host behind it."""

    def __init__(self, channels: int) -> None:
        self.module_address = "C9A5"
        self.module_channel_count = channels
        self.coordinator = None


_SIX_BYTES = "0305{t1:X}{mode:X}F0F0E5"
_FAMILIES: dict[str, tuple[ModuleType, int, str, dict[int, str]]] = {
    "switch_module": (switch_decoder, 12, _SIX_BYTES, SWITCH_MODE_MAPPING),
    "dimmer_module": (dimmer_decoder, 12, "00000{t2:X}00{t1:X}{mode:X}040000", DIMMER_MODE_MAPPING),
    "roller_module": (shutter_decoder, 6, _SIX_BYTES, ROLLER_MODE_MAPPING),
}


def _record(module_type: str, mode_raw: int, t1_raw: int, t2_raw: int = 0) -> dict[str, Any]:
    """A link record with these nibbles, as the module's own decoder reads it."""
    decoder, channels, layout, _modes = _FAMILIES[module_type]
    payload = layout.format(mode=mode_raw, t1=t1_raw, t2=t2_raw)
    raw = [payload[i : i + 2] for i in range(0, len(payload), 2)]
    record: dict[str, Any] | None = decoder.decode(payload, raw, _Context(channels))
    assert record is not None
    return record


def _explained(module_type: str, record: dict[str, Any]) -> LinkMeaning | None:
    return describe_link(module_type, record["M"], record["T1"], record["T2"])


@pytest.mark.parametrize(
    ("module_type", "mode_raw", "t1_raw", "t2_raw", "keys", "parameters", "said"),
    [
        pytest.param("switch_module", 0, 0, 0, 2, {}, "upper one switches the output on", id="switch-on-off"),
        pytest.param("switch_module", 2, 0, 0, 1, {}, "Switches the output off.", id="switch-off-no-hold"),
        pytest.param(
            "switch_module", 2, 2, 0, 1, {"hold_time": "2 s"}, "held for 2 s first", id="switch-off-held"
        ),
        pytest.param(
            "switch_module", 5, 2, 0, 1, {"off_after": "2 min"}, "off again after 2 min", id="switch-delayed-off"
        ),
        pytest.param(
            "switch_module", 8, 0, 0, 1, {"off_after": "0.5 s"}, "off again after 0.5 s", id="switch-short-delay"
        ),
        pytest.param("switch_module", 6, 15, 0, 1, {"on_after": "120 min"}, "on 120 min after", id="switch-delayed-on"),
        pytest.param(
            "dimmer_module",
            0,
            0,
            2,
            2,
            {"dim_on_time": "4 s", "dim_off_time": "1 s"},
            "Dimming on takes 4 s, dimming off 1 s.",
            id="dimmer-curve-on",
        ),
        pytest.param(
            "dimmer_module",
            0,
            1,
            2,
            2,
            {"dim_on_time": "1 s", "dim_off_time": "4 s"},
            "Dimming on takes 1 s, dimming off 4 s.",
            id="dimmer-curve-off",
        ),
        pytest.param("dimmer_module", 0, 7, 2, 2, {"dim_time": "4 s"}, "Dimming takes 4 s.", id="dimmer-curve-both"),
        pytest.param(
            "dimmer_module",
            9,
            6,
            2,
            1,
            {"level": "40 % (4,0 V)", "dim_time": "4 s"},
            "Sets the output to 40 % (4,0 V)",
            id="dimmer-preset",
        ),
        pytest.param(
            "dimmer_module",
            6,
            0,
            12,
            1,
            {"off_after": "10 s", "dim_time": "2 min"},
            "dimming it off after 10 s",
            id="dimmer-delayed-off",
        ),
        pytest.param("dimmer_module", 7, 3, 5, 1, {}, "blink at a fixed rate", id="dimmer-blink-ignores-wheels"),
        pytest.param(
            "roller_module", 0, 11, 0, 2, {"run_time": "30 s"}, "The motor runs for 30 s.", id="roller-two-keys"
        ),
        pytest.param(
            "roller_module", 1, 0, 0, 1, {"run_time": "switched off"}, "run time is switched off", id="roller-no-run"
        ),
        pytest.param("roller_module", 2, 1, 0, 1, {"run_time": "0.4 s"}, "runs for 0.4 s", id="roller-impulse"),
        pytest.param(
            "roller_module",
            5,
            11,
            0,
            1,
            {"run_time": "30 s", "hold_time": "2 s"},
            "held for 2 s first. The motor runs for 30 s.",
            id="roller-held-open",
        ),
        pytest.param("roller_module", 6, 1, 0, 1, {"hold_time": "1 s"}, "Closes the shutter.", id="roller-held-no-run"),
    ],
)
def test_link_in_words(
    module_type: str, mode_raw: int, t1_raw: int, t2_raw: int, keys: int, parameters: dict[str, str], said: str
) -> None:
    meaning = _explained(module_type, _record(module_type, mode_raw, t1_raw, t2_raw))
    assert meaning is not None
    assert meaning.keys == keys
    assert meaning.parameters == parameters
    assert said in meaning.summary


@pytest.mark.parametrize("module_type", list(_FAMILIES))
def test_every_decoded_mode_and_value_is_explained(module_type: str) -> None:
    """No mode the decoder knows is without a sentence, and no value it
    labels is dropped, except a hold time of nothing."""
    for mode_raw, label in _FAMILIES[module_type][3].items():
        for t1_raw in range(16):
            record = _record(module_type, mode_raw, t1_raw)
            meaning = _explained(module_type, record)
            assert meaning is not None, label
            assert meaning.mode == label[:3]
            assert "{" not in meaning.summary
            no_hold = "".join(str(record["T1"]).split()) in ("0s", "-/1s", "-/2s", "-/3s")
            assert meaning.parameters or record["T1"] is None or no_hold, (label, record["T1"])


def test_missing_value_is_not_invented() -> None:
    """A delay the record did not carry is named as set, not as a number."""
    meaning = describe_link("switch_module", "M06 (Delayed off (up to 2h))")
    assert meaning is not None
    assert meaning.parameters == {}
    assert meaning.summary == "Switches the output on at once, and off again after the set time."


@pytest.mark.parametrize(
    ("module_type", "mode"),
    [
        pytest.param("rgb_module", "M19 (Start/stop scenario)", id="colour-controller"),
        pytest.param("switch_module", "M09", id="mode-not-in-the-manual"),
        pytest.param("switch_module", None, id="no-mode"),
        pytest.param(None, "M01", id="no-module-type"),
    ],
)
def test_what_the_manuals_do_not_cover_is_not_explained(module_type: str | None, mode: str | None) -> None:
    assert describe_link(module_type, mode) is None


@pytest.mark.parametrize(
    ("key", "position"),
    [
        pytest.param("1A", "upper_left", id="upper-left"),
        pytest.param("1b", "lower_left", id="lower-left"),
        pytest.param("2C", "upper_right", id="second-half-of-an-eight-key-plate"),
        pytest.param("1D", "lower_right", id="lower-right"),
        pytest.param("IR:02A", None, id="an-ir-code-has-no-place"),
        pytest.param(None, None, id="no-key"),
    ],
)
def test_a_keys_place_on_its_plate(key: str | None, position: str | None) -> None:
    assert key_position(key) == position


@pytest.mark.parametrize(
    ("module_type", "mode_raw", "key", "said"),
    [
        pytest.param("switch_module", 0, "1C", "This key switches the output on;", id="switch-upper"),
        pytest.param("switch_module", 0, "1D", "This key switches the output off;", id="switch-lower"),
        pytest.param("dimmer_module", 0, "1A", "holding it dims up", id="dimmer-upper"),
        pytest.param("dimmer_module", 0, "1B", "holding it dims down", id="dimmer-lower"),
        pytest.param("roller_module", 0, "2A", "This key opens the shutter", id="roller-upper"),
        pytest.param("roller_module", 0, "2B", "This key closes the shutter", id="roller-lower"),
        pytest.param("dimmer_module", 8, "1C", "Holding this key dims the output up.", id="preset-upper-right"),
        pytest.param("roller_module", 4, "1D", "This key stops the shutter.", id="rf-lower-right"),
        pytest.param("switch_module", 0, "IR:02A", "Two keys: the upper one", id="an-ir-code-keeps-the-pair"),
        pytest.param("switch_module", 5, "1C", "Switches the output on at once", id="a-one-key-mode-is-unchanged"),
    ],
)
def test_a_mode_on_several_keys_is_said_for_the_key_it_is_on(
    module_type: str, mode_raw: int, key: str, said: str
) -> None:
    record = _record(module_type, mode_raw, 2, 2)
    meaning = describe_link(module_type, record["M"], record["T1"], record["T2"], key=key)
    assert meaning is not None
    assert said in meaning.summary
    assert "{" not in meaning.summary
