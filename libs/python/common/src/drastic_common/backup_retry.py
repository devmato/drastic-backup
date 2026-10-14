"""Bounded backup retry policy and conservative transport-error classification."""

from marshmallow import EXCLUDE, Schema, fields, validate

from drastic_common.restic.exceptions import ResticFailedError, ResticTimeoutError


class BackupRetrySchema(Schema):
    class Meta:
        unknown = EXCLUDE

    enabled = fields.Boolean(load_default=False)
    max_attempts = fields.Integer(strict=True, load_default=3, validate=validate.Range(min=1, max=6))
    delay_minutes = fields.Integer(strict=True, load_default=10, validate=validate.Range(min=1, max=1440))


def retry_allowed(policy, attempt, state, data):
    """Only a completed, explicitly classified backup failure may be repeated."""
    return bool(policy.get("enabled") and attempt < policy["max_attempts"]
                and state in {"failed", "warning"} and data.get("backup_retryable")
                and not data.get("backup_data_complete") and not data.get("backup_cleanup_pending"))


def is_transient_backup_error(error):
    if isinstance(error, ResticTimeoutError):
        return True  # The Restic client terminates its managed processes before raising.
    if not isinstance(error, ResticFailedError):
        return False
    message = str(error).lower()
    # Producer errors and permanent repository failures take precedence over a broken pipe.
    if any(text in message for text in (
        "backup source command", "permission denied", "access denied", "no space left", "disk quota",
        "wrong password", "no key found", "corrupt", "invalid data", "hash mismatch", "integrity",
        "repository is already locked", "unable to create lock",
    )):
        return False
    return any(text in message for text in (
        "connection reset by peer", "connection refused", "connection timed out", "connection lost",
        "network is unreachable", "no route to host", "temporary failure in name resolution",
        "client_loop: send disconnect: broken pipe", "i/o timeout", "tls handshake timeout",
    ))
