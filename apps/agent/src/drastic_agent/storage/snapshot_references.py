"""Atomically replace a completion-tagged snapshot in local history, outbox and CBT."""

import json
from contextlib import nullcontext

from drastic_agent.storage.database import (
    agent_operation_artifacts,
    agent_operation_queue,
    agent_operations,
    db,
    proxmox_checkpoints,
)


def require_completion(task):
    """Check the durable full-data proof and local artifact before any repository mutation."""
    operation = agent_operations.find_one(uuid=task["operation_uuid"])
    data = (operation or {}).get("data") or {}
    if (not operation or operation["repository_id"] != task["args"]["repository_id"]
            or data.get("owner_uuid") != task["owner_uuid"] or data.get("job_uuid") != task["job_uuid"]
            or not data.get("backup_data_complete")):
        raise ValueError("Completion intent no longer belongs to this full backup")
    artifact = agent_operation_artifacts.find_one(operation_id=operation["id"], uuid=task["artifact_uuid"])
    old_id = task["snapshot_id"]
    canonical_id = data.get("snapshot_replacements", {}).get(old_id)
    if (not artifact or artifact["artifact_key"] != task["artifact_key"] or artifact["state"] != "success" or not artifact["snapshot_id"]
            or artifact["snapshot_id"] not in {old_id, canonical_id}):
        raise ValueError("Completion artifact changed; reconciliation stopped")
    return operation, artifact


def replace_snapshot(task, new_id, report=None):
    """Commit references before clearing the completion intent or publishing the new ID."""
    old_id = task["snapshot_id"]
    with report._lock if report is not None else nullcontext():
        with db:
            operation, artifact = require_completion(task)
            data = operation["data"] or {}
            if data.get("snapshot_replacements", {}).get(old_id, new_id) != new_id:
                raise ValueError("Conflicting completion snapshot replacement")
            replacements = {**data.get("snapshot_replacements", {}), old_id: new_id}
            data = {**data, "snapshot_replacements": replacements, "backup_run_complete": True}
            artifact["snapshot_id"] = new_id
            agent_operation_artifacts.update(artifact, ["id"])
            agent_operations.update({"id": operation["id"], "data": data}, ["id"])
            checkpoint_key = task.get("checkpoint_key")
            checkpoint = proxmox_checkpoints.find_one(key=checkpoint_key) if checkpoint_key else None
            if checkpoint and checkpoint["data"].get("snapshot_id") == old_id:
                proxmox_checkpoints.update({"key": checkpoint_key, "data": {
                    **checkpoint["data"], "snapshot_id": new_id}}, ["key"])
            queued = agent_operation_queue.find_one(uuid=operation["uuid"])
            if queued:
                payload = json.loads(queued["payload"])
                payload["data"].update(snapshot_replacements=replacements, backup_run_complete=True)
                for item in payload["artifacts"]:
                    if item["uuid"] == task["artifact_uuid"]:
                        item["snapshot_id"] = new_id
                agent_operation_queue.update({"uuid": operation["uuid"], "payload": json.dumps(payload)}, ["uuid"])
        if report is not None:
            report.set_data("snapshot_replacements", replacements)
            report.set_data("backup_run_complete", True)
            for item in report.artifacts:
                if item["uuid"] == task["artifact_uuid"]:
                    item["snapshot_id"] = new_id
            if report.history_operation is not None:
                report.history_operation["data"] = dict(report.data)
