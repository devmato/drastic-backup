"""Identify logical Drastic backup runs without requiring a live job or report."""

from uuid import UUID


def snapshot_uuid(snapshot, name):
    values = [tag[len(name) + 1:] for tag in snapshot.get("tags") or [] if tag.startswith(name + ":")]
    if len(values) == 1:
        try:
            return str(UUID(values[0]))
        except ValueError:
            pass
    return None


def restore_point_key(snapshot):
    job, operation = snapshot_uuid(snapshot, "job_uuid"), snapshot_uuid(snapshot, "operation_uuid")
    owner = snapshot_uuid(snapshot, "drastic-owner")
    namespace = f"{owner}:" if owner else ""
    return f"run:{namespace}{job}:{operation}" if job and operation else f"snapshot:{snapshot['id']}"
