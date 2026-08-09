"""`msgs` bookkeeping: old_msgs, rollback and reconnect replay."""

from __future__ import annotations

from dcssbot.msglog import MessageLog


def msgs(*texts: str, **extra) -> dict:
    return {"msg": "msgs", "messages": [{"text": t, "turn": 1} for t in texts], **extra}


def test_plain_batch_passes_through() -> None:
    log = MessageLog()
    lines = log.feed(msgs("You hit the rat.", "The rat dies!"))
    assert [line.text for line in lines] == ["You hit the rat.", "The rat dies!"]


def test_format_markup_is_stripped() -> None:
    log = MessageLog()
    lines = log.feed(msgs("<lightred>You die...</lightred>"))
    assert lines[0].text == "You die..."


def test_blank_lines_are_dropped() -> None:
    log = MessageLog()
    lines = log.feed(msgs("", "   ", "real"))
    assert [line.text for line in lines] == ["real"]


def test_old_msgs_skips_the_repeated_prefix() -> None:
    # The client rolls back that many displayed lines and re-adds the array,
    # so the first `old_msgs` entries are ones we have already relayed.
    log = MessageLog()
    lines = log.feed(msgs("old one", "old two", "new one", old_msgs=2))
    assert [line.text for line in lines] == ["new one"]


def test_rollback_is_counted_but_does_not_skip() -> None:
    # rollback retracts previously-sent lines; the array holds new content,
    # not replacements, so nothing is skipped.
    log = MessageLog()
    lines = log.feed(msgs("Really quit? [y/n]", rollback=1))
    assert [line.text for line in lines] == ["Really quit? [y/n]"]
    assert log.rolled_back == 1


def test_repeats_during_play_are_kept() -> None:
    # Blanket de-duplication would eat this, which is why it is not done.
    log = MessageLog()
    log.feed(msgs("You hit the rat."))
    lines = log.feed(msgs("You hit the rat."))
    assert [line.text for line in lines] == ["You hit the rat."]


def test_reconnect_suppresses_a_replayed_overlap() -> None:
    log = MessageLog()
    log.feed(msgs("You see a rat.", "You hit the rat."))
    log.note_reconnect()
    lines = log.feed(msgs("You see a rat.", "You hit the rat.", "The rat dies!"))
    assert [line.text for line in lines] == ["The rat dies!"]


def test_reconnect_suppression_applies_to_one_batch_only() -> None:
    log = MessageLog()
    log.feed(msgs("You hit the rat."))
    log.note_reconnect()
    log.feed(msgs("You hit the rat."))
    lines = log.feed(msgs("You hit the rat."))
    assert [line.text for line in lines] == ["You hit the rat."]


def test_reconnect_with_no_overlap_keeps_everything() -> None:
    log = MessageLog()
    log.feed(msgs("You see a rat."))
    log.note_reconnect()
    lines = log.feed(msgs("A gnoll comes into view."))
    assert [line.text for line in lines] == ["A gnoll comes into view."]


def test_channels_can_be_filtered() -> None:
    log = MessageLog(drop_channels=(5,))
    payload = {
        "msg": "msgs",
        "messages": [
            {"text": "kept", "channel": 0},
            {"text": "dropped", "channel": 5},
        ],
    }
    assert [line.text for line in log.feed(payload)] == ["kept"]


def test_non_msgs_payloads_are_ignored() -> None:
    log = MessageLog()
    assert log.feed({"msg": "msgs"}) == []
    assert log.feed({"msg": "msgs", "messages": "nope"}) == []
    assert log.feed({"msg": "msgs", "messages": [None, 3]}) == []


def test_turn_and_channel_are_preserved() -> None:
    log = MessageLog()
    line = log.feed({"msg": "msgs", "messages": [{"text": "hi", "turn": 42, "channel": 3}]})[0]
    assert (line.turn, line.channel) == (42, 3)
