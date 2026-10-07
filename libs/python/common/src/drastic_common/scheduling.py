"""Shared minute-resolution schedules. Calendar rules use croniter; intervals use UTC."""

from calendar import monthrange
from datetime import date, datetime, timezone

from croniter import CroniterBadDateError, croniter
from marshmallow import (
    EXCLUDE,
    Schema,
    ValidationError,
    fields,
    post_load,
    validate,
    validates_schema,
)

TYPES = ("hourly", "daily", "weekly", "monthly", "yearly", "once", "periodic", "cron")


class TimingSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    type = fields.String(required=True, validate=validate.OneOf(TYPES))
    hour = fields.Integer(strict=True, validate=validate.Range(min=0, max=23))
    minute = fields.Integer(strict=True, validate=validate.Range(min=0, max=59))
    weekdays = fields.List(fields.Integer(strict=True, validate=validate.Range(min=0, max=6)), validate=validate.Length(max=7))
    day = fields.Integer(strict=True, validate=validate.Range(min=1, max=31))
    months = fields.List(fields.Integer(strict=True, validate=validate.Range(min=1, max=12)), validate=validate.Length(min=1, max=12))
    date = fields.String(validate=validate.Regexp(r"^\d{4}-\d{2}-\d{2}$"))
    from_hour = fields.Integer(strict=True, validate=validate.Range(min=0, max=23))
    to_hour = fields.Integer(strict=True, validate=validate.Range(min=0, max=23))
    interval = fields.Integer(strict=True, validate=validate.Range(min=1, max=525600))
    offset = fields.Integer(strict=True, validate=validate.Range(min=0, max=525599))
    expression = fields.String(validate=validate.Length(min=1, max=255))

    @validates_schema
    def validate_timing(self, data, **kwargs):
        kind = data["type"]
        required = {
            "hourly": ("minute",), "daily": ("hour", "minute"),
            "weekly": ("hour", "minute", "weekdays"), "monthly": ("day", "hour", "minute"),
            "yearly": ("months", "day", "hour", "minute"), "once": ("date", "hour", "minute"),
            "periodic": ("interval", "offset"), "cron": ("expression",),
        }[kind]
        missing = [key for key in required if key not in data]
        if missing:
            raise ValidationError({key: ["Required"] for key in missing})
        for key in ("weekdays", "months"):
            if len(data.get(key, [])) != len(set(data.get(key, []))):
                raise ValidationError({key: ["Duplicate values"]})
        if kind == "weekly" and not data["weekdays"]:
            raise ValidationError({"weekdays": ["Select at least one weekday"]})
        if kind == "hourly" and (("from_hour" in data) != ("to_hour" in data)):
            raise ValidationError("Specify both ends of the hour window")
        if kind == "once":
            try:
                date.fromisoformat(data["date"])
            except ValueError as exc:
                raise ValidationError({"date": ["Invalid date"]}) from exc
        if kind == "yearly" and not any(data["day"] <= monthrange(2000, month)[1] for month in data["months"]):
            raise ValidationError({"day": ["This day does not occur in the selected months"]})
        if kind == "periodic" and data["offset"] >= data["interval"]:
            raise ValidationError({"offset": ["Offset must be smaller than the interval"]})
        if kind == "cron" and (len(data["expression"].split()) != 5 or not croniter.is_valid(data["expression"])):
            raise ValidationError({"expression": ["Invalid five-field cron expression"]})

    @post_load
    def normalize(self, data, **kwargs):
        keys = {
            "hourly": ("minute", "weekdays", "from_hour", "to_hour"), "daily": ("hour", "minute"),
            "weekly": ("hour", "minute", "weekdays"), "monthly": ("day", "hour", "minute"),
            "yearly": ("months", "day", "hour", "minute"), "once": ("date", "hour", "minute"),
            "periodic": ("interval", "offset"), "cron": ("expression",),
        }[data["type"]]
        return {"type": data["type"], **{key: sorted(data[key]) if isinstance(data[key], list) else data[key]
                                       for key in keys if key in data}}


def timing_from_cron(expression):
    """Recognize old simple schedules; preserve other saved expressions losslessly."""
    parts = expression.split()
    if len(parts) == 5 and parts[0].isdigit() and parts[1].isdigit() and parts[2:4] == ["*", "*"]:
        if parts[4] == "*":
            return {"type": "daily", "hour": int(parts[1]), "minute": int(parts[0])}
        if all(value.isdigit() and 0 <= int(value) <= 6 for value in parts[4].split(',')):
            return {"type": "weekly", "hour": int(parts[1]), "minute": int(parts[0]),
                    "weekdays": sorted(int(value) for value in parts[4].split(','))}
    return {"type": "cron", "expression": expression}


def as_cron(timing):
    kind = timing["type"]
    if kind == "cron":
        return timing["expression"]
    if kind == "once":
        return ""
    if kind == "periodic":
        interval, offset = timing["interval"], timing["offset"]
        if 60 % interval == 0:
            return f"{','.join(str(value) for value in range(offset, 60, interval))} * * * *"
        if interval % 60 == 0 and 1440 % interval == 0:
            return f"{offset % 60} {','.join(str(value) for value in range(offset // 60, 24, interval // 60))} * * *"
        return ""  # e.g. */45 is NOT a constant 45-minute interval.
    minute, hour = timing["minute"], timing.get("hour", "*")
    day, months, weekdays = "*", "*", "*"
    if kind == "hourly":
        if "from_hour" in timing:
            start, end = timing["from_hour"], timing["to_hour"]
            hours = range(start, end + 1) if start <= end else [*range(start, 24), *range(end + 1)]
            hour = ','.join(str(value) for value in hours)
    if kind in {"hourly", "weekly"} and timing.get("weekdays"):
        weekdays = ','.join(map(str, timing["weekdays"]))
    if kind in {"monthly", "yearly"}:
        day = timing["day"]
    if kind == "yearly":
        months = ','.join(map(str, timing["months"]))
    return f"{minute} {hour} {day} {months} {weekdays}"


def _once_at(timing, now):
    return datetime.fromisoformat(timing["date"]).replace(hour=timing["hour"], minute=timing["minute"], tzinfo=now.tzinfo)


def matches(timing, now):
    if timing["type"] == "once":
        return now.replace(second=0, microsecond=0) == _once_at(timing, now)
    expression = as_cron(timing)
    if timing["type"] == "periodic":
        now = now.astimezone(timezone.utc)
        if not expression:
            return (int(now.timestamp()) // 60 - timing["offset"]) % timing["interval"] == 0
    return croniter.match(expression, now)


def next_run(timing, now):
    """Calendar times follow `now`'s zone; naive `now` means agent-local time."""
    if timing["type"] == "once":
        candidate = _once_at(timing, now)
        if candidate.tzinfo is None and candidate.astimezone().replace(tzinfo=None) != candidate:
            return None
        return candidate if candidate > now else None
    expression = as_cron(timing)
    if timing["type"] == "periodic":
        now = now.astimezone(timezone.utc)
        if not expression:
            minutes = int(now.timestamp()) // 60
            interval, offset = timing["interval"], timing["offset"]
            return datetime.fromtimestamp(((minutes - offset) // interval + 1) * interval * 60 + offset * 60, timezone.utc)
    try:
        iterator = croniter(expression, now, max_years_between_matches=8)
        for _ in range(180):
            candidate = iterator.get_next(datetime)
            # Skip nonexistent local wall times at the spring DST transition.
            if candidate.tzinfo is not None or candidate.astimezone().replace(tzinfo=None) == candidate:
                return candidate
    except CroniterBadDateError:
        return None
    return None


def slot_key(timing, now):
    if timing["type"] == "once":
        return "once:" + _once_at(timing, now).replace(tzinfo=None).isoformat()
    return now.astimezone(timezone.utc).replace(second=0, microsecond=0).isoformat()


def describe(timing):
    kind = timing["type"]
    if kind == "cron":
        return timing["expression"]
    if kind == "periodic":
        return f"Every {timing['interval']} minutes, offset {timing['offset']} minutes"
    at = f"{timing.get('hour', 0):02d}:{timing['minute']:02d}"
    days = ', '.join(('Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat')[day] for day in timing.get('weekdays', []))
    if kind == "hourly":
        window = f", hours {timing['from_hour']:02d}–{timing['to_hour']:02d}" if 'from_hour' in timing else ''
        return f"Hourly at minute {timing['minute']:02d}" + (f", {days}" if days else '') + window
    if kind == "daily":
        return f"Daily at {at}"
    if kind == "weekly":
        return f"{days} at {at}"
    if kind == "monthly":
        return f"Day {timing['day']} of each month at {at}"
    if kind == "yearly":
        months = ', '.join(date(2000, month, 1).strftime('%b') for month in timing['months'])
        return f"{months}, day {timing['day']} at {at}"
    return f"Once on {timing['date']} at {at}"


def preview(timing, now):
    upcoming = next_run(timing, now)
    if upcoming is not None and now.tzinfo is None:
        upcoming = upcoming.astimezone()
    return {"description": describe(timing), "next_run": upcoming.isoformat() if upcoming else None,
            "next_run_text": upcoming.strftime('%Y-%m-%d %H:%M %Z') if upcoming else None,
            "timezone": now.astimezone().tzname() if now.tzinfo is None else str(now.tzinfo)}
