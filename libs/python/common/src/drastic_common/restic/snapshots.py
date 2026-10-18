"""Identify logical Drastic backup runs without requiring a live job or report."""

from uuid import UUID

RUN_COMPLETE_TAG = "drastic-run-complete"


def snapshot_tag(snapshot, name):
    values = [tag[len(name) + 1:] for tag in snapshot.get("tags") or [] if tag.startswith(name + ":")]
    return values[0] if len(values) == 1 and values[0] else None


def snapshot_uuid(snapshot, name):
    value = snapshot_tag(snapshot, name)
    if value:
        try:
            return str(UUID(value))
        except ValueError:
            pass
    return None


def snapshot_identity(snapshot):
    """Return an unambiguous owner/job/run/artifact identity, never a partial namespace."""
    identity = tuple(snapshot_uuid(snapshot, name) for name in
                     ("drastic-owner", "job_uuid", "operation_uuid", "artifact_uuid"))
    artifact_key = snapshot_tag(snapshot, "artifact_key")
    return (*identity, artifact_key) if all(identity) and artifact_key else None


def run_is_complete(snapshot):
    return snapshot_identity(snapshot) is not None and (snapshot.get("tags") or []).count(RUN_COMPLETE_TAG) == 1


def restore_point_key(snapshot):
    job, operation = snapshot_uuid(snapshot, "job_uuid"), snapshot_uuid(snapshot, "operation_uuid")
    owner = snapshot_uuid(snapshot, "drastic-owner")
    namespace = f"{owner}:" if owner else ""
    return f"run:{namespace}{job}:{operation}" if job and operation else f"snapshot:{snapshot['id']}"
