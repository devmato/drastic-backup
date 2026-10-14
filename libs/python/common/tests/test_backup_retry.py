import pytest
from marshmallow import ValidationError

from drastic_common.backup_retry import BackupRetrySchema, is_transient_backup_error, retry_allowed
from drastic_common.restic.exceptions import (
    ResticCancelledError,
    ResticFailedError,
    ResticTimeoutError,
)


def test_policy_defaults_limits_and_completed_backup_guards():
    assert BackupRetrySchema().load({}) == {"enabled": False, "max_attempts": 3, "delay_minutes": 10}
    for key, values in {"max_attempts": [0, 7, True, 2.5, "3"], "delay_minutes": [0, 1441, None]}.items():
        for value in values:
            with pytest.raises(ValidationError):
                BackupRetrySchema().load({key: value})
    policy = BackupRetrySchema().load({"enabled": True})
    data = {"backup_retryable": True}
    assert retry_allowed(policy, 1, "failed", data)
    assert retry_allowed(policy, 2, "warning", {**data, "partial_failure": True})
    assert not retry_allowed(policy, 3, "failed", data)
    for state in ("cancelled", "running", "success"):
        assert not retry_allowed(policy, 1, state, data)
    for guard in ("backup_data_complete", "backup_cleanup_pending"):
        assert not retry_allowed(policy, 1, "failed", {**data, guard: True})


@pytest.mark.parametrize("error, expected", [
    (ResticFailedError("subprocess ssh: Connection reset by peer\nclient_loop: send disconnect: Broken pipe"), True),
    (ResticFailedError("connection lost"), True),
    (ResticTimeoutError("Restic operation timed out"), True),
    (ResticFailedError("no space left on device; connection lost"), False),
    (ResticFailedError("Permission denied; connection lost"), False),
    (ResticFailedError("Backup source command failed: connection lost"), False),
    (ResticFailedError("hash mismatch; connection lost"), False),
    (ResticFailedError("unrecognized failure"), False),
    (ResticCancelledError("connection lost"), False),
    (RuntimeError("connection lost"), False),
])
def test_conservative_transport_classification(error, expected):
    assert is_transient_backup_error(error) is expected
