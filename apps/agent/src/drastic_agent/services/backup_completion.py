"""Confirm whole backup runs with one Restic tag and recover metadata-ID rewrites."""

import hashlib
import json
from time import time
from uuid import UUID

from drastic_agent.agent.report import AgentReport
from drastic_agent.storage.snapshot_references import replace_snapshot, require_completion
from drastic_common.restic.snapshots import RUN_COMPLETE_TAG, run_is_complete, snapshot_identity


def task_key(repository_id, operation_uuid):
    return f"backup_completion:{repository_id}:{operation_uuid}"


def _reconcile(restic, settings, name, task):
    require_completion(task)
    identity = tuple(task[key] for key in ("owner_uuid", "job_uuid", "operation_uuid", "artifact_uuid", "artifact_key"))
    snapshots = restic.snapshots() or []
    source = next((item for item in snapshots if item["id"] == task["snapshot_id"]), None)
    if source is not None and snapshot_identity(source) != identity:
        raise ValueError("Completion snapshot identity is ambiguous or belongs to another owner")
    if task.get("native_vmid") is not None and not task.get("checkpoint_key"):
        repository_uuid = restic.cat_config()["id"]
        task["checkpoint_key"] = hashlib.sha256(
            f"{task['job_uuid']}:{repository_uuid}:{task['native_vmid']}".encode()).hexdigest()
        settings.upsert({"name": name, "settings": json.dumps(task)}, ["name"])
    marked = [item for item in snapshots if snapshot_identity(item) == identity and run_is_complete(item)]
    if not marked:
        if source is None:
            raise ValueError("Completion snapshot is missing; reconciliation deferred")
        # Persist the exact source before Restic can replace its metadata and ID.
        task.update(tree=source["tree"], original_id=source.get("original") or source["id"])
        settings.upsert({"name": name, "settings": json.dumps(task)}, ["name"])
        restic.tag_snapshot(source["id"], RUN_COMPLETE_TAG)
        snapshots = restic.snapshots() or []
        marked = [item for item in snapshots if snapshot_identity(item) == identity and run_is_complete(item)]
    if (len(marked) != 1 or marked[0].get("tree") != task.get("tree")
            or marked[0].get("original") != task.get("original_id")):
        raise ValueError("Completion tag rewrite could not be verified; reconciliation deferred")
    snapshot = marked[0]
    if source is not None and source["id"] != snapshot["id"]:
        # A crash between Restic's save and remove can leave both metadata versions.
        remaining = {item["id"] for item in snapshots}
        if source["id"] in remaining:
            restic.forget_snapshots([source["id"]], prune=False)
    with AgentReport._registry_lock:
        report = AgentReport.running_operations.get(task["operation_uuid"]) or AgentReport.delivering_reports.get(task["operation_uuid"])
        if report is None:
            report = next((item for item in list(AgentReport.finished_reports) if item.uuid == task["operation_uuid"]), None)
    replace_snapshot(task, snapshot["id"], report)
    settings.delete(name=name)


def recover_repository(restic, settings, repository_id, owner_uuid):
    """Recover pending rewrites under the caller's existing repository admission."""
    for row in list(settings.all()):
        if not row["name"].startswith(f"backup_completion:{repository_id}:"):
            continue
        task = json.loads(row["settings"])
        try:
            if task["owner_uuid"] != owner_uuid:
                raise ValueError("Pending completion belongs to another backup owner")
            _reconcile(restic, settings, row["name"], task)
        except Exception:
            task["retry_at"] = time() + 900
            settings.upsert({"name": row["name"], "settings": json.dumps(task)}, ["name"])
            raise


def complete_backup(restic, settings, report, owner_uuid, job_uuid):
    """Record full-data completion before tagging; uncertain outcomes remain retryable."""
    # Serialize admission with shutdown/terminal serialization, not with Restic's network I/O.
    with report._lock:
        if report.ended or not report.data.get("backup_data_complete") or report.cancel_event.is_set():
            return
        if (len(report.artifacts) != report.data.get("backup_items_total", 1)
                or not all(item["state"] == "success" and item["snapshot_id"] for item in report.artifacts)):
            raise ValueError("Cannot confirm a partial backup run")
        artifact = report.artifacts[-1]
        task = {"owner_uuid": str(UUID(owner_uuid)), "job_uuid": str(UUID(job_uuid)),
                "operation_uuid": str(UUID(report.uuid)), "artifact_uuid": str(UUID(artifact["uuid"])),
                "artifact_key": artifact["artifact_key"], "snapshot_id": artifact["snapshot_id"], "retry_at": time() + 900,
                "args": {"repository_id": report.repository_id, "job_id": report.job_id,
                         "job_uuid": job_uuid, "current_operation_uuid": report.uuid}}
        if artifact.get("data", {}).get("backup_method") == "native":
            task["native_vmid"] = artifact["data"]["vmid"]
        report.checkpoint()
        name = task_key(report.repository_id, report.uuid)
        settings.upsert({"name": name, "settings": json.dumps(task)}, ["name"])
    _reconcile(restic, settings, name, task)
