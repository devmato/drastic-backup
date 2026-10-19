"""Retention contracts reject ambiguous or silently weakened deletion policies."""

import pytest
from marshmallow import ValidationError

from drastic_common.retention import RetentionPolicySchema


def test_all_retention_rule_shapes_round_trip():
    rules = [{"type": "last", "count": 2},
             *[{"type": "last", "count": 2, "period": period} for period in ("hour", "day", "week", "month", "year")],
             {"type": "recent", "days": 1, "weeks": 2, "months": 3},
             {"type": "since", "date": "2024-02-29"},
             {"type": "calendar", "period": "day", "count": 7},
             {"type": "calendar", "period": "week", "count": 4, "weekday": 6},
             {"type": "calendar", "period": "month", "count": 12, "day": 31},
             {"type": "calendar", "period": "year", "count": 5, "month": 2}]
    assert RetentionPolicySchema().load({"rules": rules}) == {"rules": rules}


@pytest.mark.parametrize("rule", [
    {"type": "last", "count": 0}, {"type": "last", "count": True},
    {"type": "last", "count": "2"}, {"type": "last", "count": 1.5},
    {"type": "last", "count": 1, "period": "invalid"},
    {"type": "last", "count": 1, "weekday": 0},
    {"type": "recent"}, {"type": "recent", "days": 0},
    {"type": "recent", "months": -1}, {"type": "since", "date": "2026-02-29"},
    {"type": "calendar", "period": "hour", "count": 1},
    {"type": "calendar", "period": "week", "count": 1},
    {"type": "calendar", "period": "week", "count": 1, "weekday": 7},
    {"type": "calendar", "period": "month", "count": 1, "day": 0},
    {"type": "calendar", "period": "year", "count": 1, "month": 13},
    {"type": "calendar", "period": "day", "count": 1, "month": 1},
    {"type": "unknown", "count": 1},
])
def test_invalid_rules_are_rejected(rule):
    with pytest.raises(ValidationError):
        RetentionPolicySchema().load({"rules": [rule]})


@pytest.mark.parametrize("policy", [{}, {"rules": []}, {"rules": None}, {"keep_last": 1},
                                   {"rules": [{"type": "last", "count": 1}] * 101}])
def test_empty_legacy_or_excessive_policies_are_rejected(policy):
    with pytest.raises(ValidationError):
        RetentionPolicySchema().load(policy)
