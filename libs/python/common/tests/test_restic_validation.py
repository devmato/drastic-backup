import pytest
from marshmallow import ValidationError

from drastic_common.agent.schemas import AgentRepositoryCheckConfigSchema
from drastic_common.restic.client import ResticApi
from drastic_common.restic.validation import validate_read_data_subset


@pytest.mark.parametrize("value,expected", [(None, None), ("", None), (" 5% ", "5%"),
    ("100%", "100%"), (".5%", ".5%"), ("1e-2%", "1e-2%"), ("1/256", "1/256"),
    ("256/256", "256/256"), ("1B", "1B"), ("10k", "10k"), ("100M", "100M"), ("1G", "1G"), ("2T", "2T")])
def test_valid_check_subsets(value, expected):
    assert validate_read_data_subset(value) == expected


@pytest.mark.parametrize("value", ["banana", "5", "0%", "101%", "NaN%", "inf%", "0/10", "11/10",
    "1/257", "1/0", "-1/2", "0G", "-1G", "1.5G", "1GB", "99999999999999999999999T"])
def test_invalid_check_subsets_never_start_restic(value):
    with pytest.raises(ValueError):
        ResticApi("must-not-execute").check(read_data_subset=value)
    with pytest.raises(ValidationError):
        AgentRepositoryCheckConfigSchema().load({"enabled": True, "read_data": value})


def test_disabled_checks_ignore_stale_invalid_settings():
    assert AgentRepositoryCheckConfigSchema().load({"enabled": False, "read_data": "banana"}) == {
        "enabled": False, "read_data": None,
    }
