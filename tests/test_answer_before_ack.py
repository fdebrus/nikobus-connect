"""A state answer that arrives before our own ack is not ours.

A Feedback Module polls the output modules on its own; its queries are
not visible on every gateway, and the ``$1C`` answer to one of them can
land while the command handler waits for the answer to *its* query of
the same module — possibly for the other output group. The handler
now holds such an early frame instead of taking it, and uses it only
when nothing fresher follows the interface's ack.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from nikobus_connect.command import NikobusCommandHandler

PUSHED = "$1C6C0E00FF0000FF0000508A88"   # group 2 answer to the feedback module's query
OURS = "$1C6C0E00730000FF00008F49C5"     # answer to our own group-1 query
ACK = "$0512"
WAIT_ANSWER = "$1C6C0E"


def _handler(frames: list[str]) -> NikobusCommandHandler:
    listener = MagicMock()
    listener.response_queue = asyncio.Queue()
    for f in frames:
        listener.response_queue.put_nowait(f)
    return NikobusCommandHandler(connection=MagicMock(), listener=listener, module_states={})


async def test_answer_after_the_ack_wins_over_an_early_one() -> None:
    handler = _handler([PUSHED, ACK, OURS])
    with patch("nikobus_connect.command.COMMAND_POST_ACK_ANSWER_TIMEOUT", 0.2):
        state = await handler._wait_for_ack_and_answer_state(ACK, WAIT_ANSWER)
    assert state == "730000FF0000"


async def test_early_answer_is_used_when_nothing_follows_the_ack() -> None:
    """A gateway that sends the answer before the ack still works."""
    handler = _handler([OURS, ACK])
    with (
        patch("nikobus_connect.command.COMMAND_POST_ACK_ANSWER_TIMEOUT", 0.2),
        patch("nikobus_connect.command.COMMAND_ACK_WAIT_TIMEOUT", 1),
    ):
        state = await handler._wait_for_ack_and_answer_state(ACK, WAIT_ANSWER)
    assert state == "730000FF0000"


async def test_early_answer_without_ack_is_not_an_answer() -> None:
    handler = _handler([PUSHED])
    with (
        patch("nikobus_connect.command.COMMAND_ANSWER_WAIT_TIMEOUT", 0.2),
        patch("nikobus_connect.command.COMMAND_ACK_WAIT_TIMEOUT", 1),
    ):
        state = await handler._wait_for_ack_and_answer_state(ACK, WAIT_ANSWER)
    assert state is None


async def test_clock_and_raw_replies_are_not_held() -> None:
    """Only output-state answers can be pushed by a feedback module."""
    handler = _handler(["$1CFFF58600" + "1A090817" + "2C00" + "ABCD12", "$051D"])
    with patch("nikobus_connect.command.COMMAND_POST_ACK_ANSWER_TIMEOUT", 0.2):
        state = await handler._wait_for_ack_and_answer_state("$051D", "$1CFFF586", raw=True)
    assert state is not None
