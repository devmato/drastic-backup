import logging
from time import time

from drastic_agent.agent.database import truenas_snapshots
from drastic_agent.agent.enums import AgentOperationState
from drastic_agent.jobs.base import BackupJobHandler
from drastic_agent.truenas import TRUENAS_LOCK, TrueNASError
from drastic_common.restic.exceptions import ResticCancelledError
from drastic_common.truenas import TrueNASBackupConfigSchema


def cleanup_snapshots(api, agent_id, report=None):
    """Called under TRUENAS_LOCK, including before jobs after an interrupted run."""
    pending = list(truenas_snapshots.all())
    if not pending:
        return
    host_id = api.call("system.host_id")
    for row in pending:
        try:
            if row["api_url"] != api.settings["api_url"] or row["host_id"] != host_id:
                raise TrueNASError("Pending snapshot belongs to a different NAS; restore its connection to clean it up")
            snapshots = api.call("pool.snapshot.query", [["id", "=", row["snapshot_id"]]])
            if snapshots:
                properties = snapshots[0].get("properties") or {}
                if (properties.get("org.drastic:agent", {}).get("value") != str(agent_id)
                        or properties.get("org.drastic:operation", {}).get("value") != row["operation_uuid"]):
                    raise TrueNASError("Snapshot ownership does not match; refusing deletion")
                api.call("pool.snapshot.delete", row["snapshot_id"], {"recursive": False, "defer": False})
            elif not row["confirmed"] and time() - row["created_at"] < 300:
                # An API timeout is not proof that snapshot creation failed.
                raise TrueNASError("Snapshot creation outcome is unknown; retry cleanup after five minutes")
            truenas_snapshots.delete(id=row["id"])
            if report:
                report.log_message(f"Cleaned up TrueNAS snapshot {row['snapshot_id']}")
        except Exception as exc:
            message = f"TrueNAS cleanup pending for {row['snapshot_id']}: {exc}"
            if report:
                report.log_message(message, final_state=AgentOperationState.warning)
            else:
                logging.warning(message)


def recover_snapshots(agent):
    if not TRUENAS_LOCK.acquire(blocking=False):
        return
    try:
        cleanup_snapshots(agent.get_truenas_client(), agent.identifier)
    except Exception as exc:
        logging.warning("TrueNAS snapshot recovery pending: %s", exc)
    finally:
        TRUENAS_LOCK.release()


class TrueNASBackupJobHandler(BackupJobHandler):
    def run_backup(self, report):
        config = TrueNASBackupConfigSchema().load(self.job.get("config") or {})
        if not TRUENAS_LOCK.acquire(blocking=False):
            raise TrueNASError("Another TrueNAS backup or connection change is in progress")
        api = None
        try:
            api = self.agent.get_truenas_client()
            self._check_cancelled(report)
            api.version()
            cleanup_snapshots(api, self.agent.identifier, report)
            if truenas_snapshots.count():
                raise TrueNASError("Clean up pending snapshots before starting another TrueNAS backup")
            available = {item["id"]: item for item in api.datasets()}
            names = set(config["datasets"])
            if config["include_children"]:
                prefixes = tuple(f"{parent}/" for parent in config["datasets"])
                names.update(name for name in available if name.startswith(prefixes))
            selected = []
            for name in sorted(names):
                dataset = available.get(name)
                if not dataset or not dataset["available"]:
                    raise TrueNASError(f"Dataset {name}: {dataset['error'] if dataset else 'not found or unsupported'}")
                selected.append(dataset)
            host_id = api.call("system.host_id")
            name = f"drastic-{report.uuid}"
            paths = []
            report.set_data("backup_items_total", len(selected))
            report.set_data("truenas_progress", {"phase": "snapshots", "datasets_total": len(selected)})
            for dataset in selected:
                self._check_cancelled(report)
                snapshot_id = f"{dataset['id']}@{name}"
                record = {"snapshot_id": snapshot_id, "api_url": api.settings["api_url"], "host_id": host_id,
                          "operation_uuid": report.uuid, "created_at": time(), "confirmed": False}
                record["id"] = truenas_snapshots.insert(record)
                api.call("pool.snapshot.create", {
                    "dataset": dataset["id"], "name": name, "recursive": False,
                    "properties": {"org.drastic:agent": str(self.agent.identifier), "org.drastic:operation": report.uuid},
                })
                record["confirmed"] = True
                truenas_snapshots.update(record, ["id"])
                paths.append((dataset, api.snapshot_path(dataset, name)))
                report.log_message(f"Created and mounted {snapshot_id}")

            failed = []
            for index, (dataset, path) in enumerate(paths, 1):
                self._check_cancelled(report)
                report.set_data("truenas_progress", {"phase": "backup", "dataset": dataset["id"],
                                                    "dataset_index": index, "datasets_total": len(paths)})
                artifact = self.start_artifact(f"dataset:{dataset['id']}", report=report, data={
                    "dataset": dataset["id"], "mountpoint": dataset["mountpoint"], "zfs_snapshot": f"{dataset['id']}@{name}",
                })
                try:
                    with self.agent.resticapi.operation_cancellation(report.cancel_event):
                        previous = self.agent.resticapi.snapshots(tags=[f"job_uuid:{self.job['uuid']},dataset:{dataset['id']}"])
                        parent = max(previous, key=lambda item: item["time"])["id"] if previous else None
                        status = self.agent.resticapi.backup(
                            paths=["."], cwd=str(path), parent=parent,
                            exclude_patterns=[f"{path}/{pattern.lstrip('/')}" for pattern in [".zfs", *config["exclude_patterns"]]],
                            tags=[f"job_uuid:{self.job['uuid']}", f"operation_uuid:{report.uuid}", "source:truenas",
                                  f"dataset:{dataset['id']}", f"artifact_uuid:{artifact['uuid']}",
                                  f"artifact_key:{artifact['artifact_key']}"],
                            callback=report.process_backup_status,
                            callback_pid=True, callback_throttle=500,
                        )
                    report.process_backup_status(status)
                    self.finish_artifact(artifact, snapshot_id=status.get("snapshot_id"))
                except Exception as exc:
                    self.finish_artifact(artifact, state=AgentOperationState.failed, snapshot_id=getattr(exc, "snapshot_id", None))
                    self._check_cancelled(report)
                    if isinstance(exc, ResticCancelledError):
                        raise
                    failed.append(dataset["id"])
                    report.log_message(f"Dataset {dataset['id']} failed: {exc}")
            if failed:
                report.set_data("partial_failure", len(failed) < len(paths))
                raise TrueNASError(f"Backup failed for dataset(s): {', '.join(failed)}")
        finally:
            try:
                if api:
                    report.set_data("truenas_progress", {**report.data.get("truenas_progress", {}), "phase": "cleanup"})
                    try:
                        cleanup_snapshots(api, self.agent.identifier, report)
                    except Exception as exc:
                        report.log_message(f"TrueNAS cleanup pending: {exc}", final_state=AgentOperationState.warning)
            finally:
                TRUENAS_LOCK.release()

    @staticmethod
    def _check_cancelled(report):
        if report.cancel_event.is_set() or report.final_state == AgentOperationState.cancelled:
            raise ResticCancelledError("TrueNAS backup cancelled")
