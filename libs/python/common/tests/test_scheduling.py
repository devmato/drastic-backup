import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from marshmallow import ValidationError

from drastic_common.scheduling import (
    TimingSchema,
    as_cron,
    matches,
    next_run,
    preview,
    slot_key,
    timing_from_cron,
)


@pytest.mark.parametrize("timing,after,expected", [
    ({"type": "hourly", "minute": 15}, "2026-10-07T02:16:00", "2026-10-07T03:15:00"),
    ({"type": "hourly", "minute": 15, "weekdays": [1], "from_hour": 8, "to_hour": 18}, "2026-10-09T20:00:00", "2026-10-12T08:15:00"),
    ({"type": "hourly", "minute": 0, "from_hour": 22, "to_hour": 2}, "2026-10-07T03:00:00", "2026-10-07T22:00:00"),
    ({"type": "daily", "hour": 4, "minute": 30}, "2026-10-07T04:30:00", "2026-10-08T04:30:00"),
    ({"type": "weekly", "weekdays": [1, 5], "hour": 2, "minute": 0}, "2026-10-07T02:00:00", "2026-10-09T02:00:00"),
    ({"type": "monthly", "day": 31, "hour": 2, "minute": 0}, "2026-10-31T02:00:00", "2026-12-31T02:00:00"),
    ({"type": "yearly", "months": [2], "day": 29, "hour": 2, "minute": 0}, "2026-10-07T02:00:00", "2028-02-29T02:00:00"),
    ({"type": "yearly", "months": [2, 4, 12], "day": 31, "hour": 2, "minute": 0}, "2026-10-07T02:00:00", "2026-12-31T02:00:00"),
    ({"type": "once", "date": "2026-12-24", "hour": 8, "minute": 0}, "2026-10-07T02:00:00", "2026-12-24T08:00:00"),
])
def test_calendar_preview_matches_execution(timing, after, expected):
    timing = TimingSchema().load(timing)
    after = datetime.fromisoformat(after).replace(tzinfo=timezone.utc)
    expected = datetime.fromisoformat(expected).replace(tzinfo=timezone.utc)
    assert next_run(timing, after) == expected
    assert matches(timing, expected)
    assert not matches(timing, expected - timedelta(minutes=1))


@pytest.mark.parametrize("interval,offset", [(15, 5), (60, 15), (120, 70), (45, 7), (90, 5), (100, 0), (1440, 120)])
def test_periodic_intervals_do_not_reset_at_hour_day_or_restart(interval, offset):
    timing = TimingSchema().load({"type": "periodic", "interval": interval, "offset": offset})
    current = datetime(2026, 10, 7, 23, 55, tzinfo=timezone.utc)
    first = next_run(timing, current)
    assert (int(first.timestamp()) // 60 - offset) % interval == 0
    assert as_cron(timing) or interval in (45, 90, 100)
    for _ in range(40):
        following = next_run(dict(timing), first)  # No process-local interval anchor.
        assert following - first == timedelta(minutes=interval)
        assert matches(timing, following)
        assert not matches(timing, following - timedelta(minutes=1))
        first = following


@pytest.mark.parametrize("value", [
    {"type": "periodic", "interval": 0, "offset": 0},
    {"type": "periodic", "interval": 60, "offset": 60},
    {"type": "periodic", "interval": 1.5, "offset": 0},
    {"type": "weekly", "hour": 2, "minute": 0, "weekdays": []},
    {"type": "weekly", "hour": 2, "minute": 0, "weekdays": [1, 1]},
    {"type": "yearly", "hour": 2, "minute": 0, "day": 30, "months": [2]},
    {"type": "once", "hour": 2, "minute": 0, "date": "2026-02-29"},
    {"type": "hourly", "minute": 0, "from_hour": 8},
    {"type": "daily", "hour": 24, "minute": 0},
    {"type": "daily", "minute": 0},
    {"type": "cron", "expression": "* * * * * *"},
])
def test_invalid_input_is_rejected(value):
    with pytest.raises(ValidationError):
        TimingSchema().load(value)


def test_once_does_not_repeat_and_legacy_cron_is_preserved():
    timing = {"type": "once", "date": "2026-10-07", "hour": 2, "minute": 0}
    now = datetime(2026, 10, 7, 2, tzinfo=timezone.utc)
    assert matches(timing, now)
    assert next_run(timing, now) is None
    assert not matches(timing, now + timedelta(days=365))
    assert slot_key(timing, now).startswith('once:')
    for expression in ('0 2 * * *', '0 2 * * 1,5', '15 3 1 * *', '*/15 * * * *'):
        assert as_cron(TimingSchema().load(timing_from_cron(expression))) == expression


def test_agent_local_preview_skips_nonexistent_dst_time(monkeypatch):
    original = os.environ.get('TZ')
    try:
        monkeypatch.setenv('TZ', 'Europe/Berlin')
        time.tzset()
        timing = {"type": "daily", "hour": 2, "minute": 30}
        result = next_run(timing, datetime(2026, 3, 29, 1))
        assert result == datetime(2026, 3, 30, 2, 30)
        assert preview(timing, datetime(2026, 3, 29, 1))["next_run"].endswith('+02:00')
        once = {"type": "once", "date": "2026-10-25", "hour": 2, "minute": 30}
        assert slot_key(once, datetime(2026, 10, 25, 2, 30, fold=0)) == slot_key(once, datetime(2026, 10, 25, 2, 30, fold=1))
    finally:
        if original is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = original
        time.tzset()
