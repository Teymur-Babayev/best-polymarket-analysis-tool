from pmanalysis.backtest.reversal_count import count_reversals


def _ticks(*rows):
    return list(rows)


def test_count_yes_then_no_reversal():
    ticks = _ticks(
        (1000, 91.0, 10.0, 200),
        (2000, 91.0, 10.0, 199),
        (3000, 10.0, 92.0, 198),
        (4000, 10.0, 93.0, 197),
    )
    count, events = count_reversals(ticks)
    assert count == 2
    assert events[0].side == "YES"
    assert events[1].side == "NO"


def test_count_three_alternations():
    ticks = _ticks(
        (1000, 90.0, 10.0, 300),
        (2000, 10.0, 90.0, 290),
        (3000, 91.0, 10.0, 280),
    )
    count, events = count_reversals(ticks)
    assert count == 3
    assert [e.side for e in events] == ["YES", "NO", "YES"]


def test_no_double_count_while_same_side_stays_extreme():
    ticks = _ticks(
        (1000, 95.0, 8.0, 100),
        (2000, 96.0, 7.0, 99),
        (3000, 97.0, 6.0, 98),
    )
    count, events = count_reversals(ticks)
    assert count == 1
    assert events[0].side == "YES"


def test_no_count_when_neither_side_hits_threshold():
    ticks = _ticks(
        (1000, 50.0, 52.0, 100),
        (2000, 55.0, 48.0, 99),
    )
    count, events = count_reversals(ticks)
    assert count == 0
    assert events == []


def test_yes_reentry_without_no_flip_does_not_count():
    ticks = _ticks(
        (1000, 90.0, 10.0, 100),
        (2000, 50.0, 50.0, 90),
        (3000, 91.0, 10.0, 80),
    )
    count, events = count_reversals(ticks)
    assert count == 1
    assert events[0].side == "YES"


def test_skip_impossible_both_sides_above_threshold():
    ticks = _ticks(
        (1000, 51.0, 50.0, 200),
        (2000, 91.0, 91.0, 199),
        (3000, 91.0, 91.0, 198),
        (4000, 10.0, 92.0, 197),
        (5000, 91.0, 10.0, 196),
    )
    count, events = count_reversals(ticks)
    assert count == 2
    assert [e.side for e in events] == ["NO", "YES"]


def test_tiered_thresholds():
    from pmanalysis.backtest.reversal_count import count_reversals_tiered

    ticks = _ticks(
        (1000, 91.0, 10.0, 300),   # flip 1 YES @ 90
        (2000, 10.0, 85.0, 290),   # flip 2 NO @ 85 (not 90)
        (3000, 75.0, 10.0, 280),   # flip 3 YES @ 75 (not 90)
    )
    count, events = count_reversals_tiered(ticks, first=90, second=85, third=75)
    assert count == 3
    assert [e.side for e in events] == ["YES", "NO", "YES"]


def test_format_event_sequence_includes_timing():
    from pmanalysis.backtest.reversal_count import ReversalEvent, format_event_sequence

    events = [
        ReversalEvent(1, "YES", 1_000_000, 91.0, 10.0, 165),
        ReversalEvent(2, "NO", 2_000_000, 10.0, 92.0, 120),
    ]
    text = format_event_sequence(events)
    assert "YES[165s@" in text
    assert "91c]" in text
    assert ">NO[120s@" in text
    assert "92c]" in text
