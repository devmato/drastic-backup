"""Back up verified ZFS snapshots with dataset-scoped Restic parents."""

import logging
import re
from datetime import datetime
from pathlib import PurePosixPath
from time import time

from drastic_agent.agent.enums import AgentOperationState
from drastic_agent.jobs.base import BackupJobHandler
from drastic_agent.storage.database import truenas_snapshots
from drastic_agent.truenas import TRUENAS_LOCK, TrueNASError
from drastic_common.restic.exceptions import ResticCancelledError
from drastic_common.truenas import TrueNASBackupConfigSchema, path_is_within


def dataset_paths(entries, dataset, available):
    """Project selected subtrees onto each dataset's own snapshot root."""
    paths = set()
    for entry in entries:
        if entry["dataset"] == dataset["id"]:
            paths.add(entry["path"])
        elif dataset["id"].startswith(entry["dataset"] + "/"):
            if entry["path"] == ".":
                paths.add(".")
            elif entry["group"] != "file" and entry["dataset"] in available and dataset.get("mountpoint"):
                source = available[entry["dataset"]].get("mountpoint")
                if source:
                    selected = PurePosixPath(source) / entry["path"]
                    mount = PurePosixPath(dataset["mountpoint"])
                    if mount.is_relative_to(selected):
                        paths.add(".")
                    elif selected.is_relative_to(mount):
                        paths.add(str(selected.relative_to(mount)))
    return ["."] if "." in paths else sorted(paths)


def backup_selection(config, available):
    for entry in config["paths"]:
        if entry["dataset"] not in available and not any(path_is_within(entry, excluded) for excluded in config["exclude_paths"]):
            raise TrueNASError(f"Dataset {entry['dataset']}: not found or unsupported")
    selected = []
    for dataset in sorted(available.values(), key=lambda item: item["id"]):
        included = dataset_paths(config["paths"], dataset, available)
        excluded = dataset_paths(config["exclude_paths"], dataset, available)
        included = [path for path in included if not any(
            parent == "." or path == parent or path.startswith(parent + "/") for parent in excluded)]
        if not included:
            continue
        if not dataset["available"]:
            raise TrueNASError(f"Dataset {dataset['id']}: {dataset['error']}")
        selected.append((dataset, included, excluded))
    if not selected:
        raise TrueNASError("Select at least one path that is not excluded")
    return selected


def literal_pattern(path):
    # Restic exclusions are glob patterns; browser selections are literal names.
    return re.sub(r"([\\*?\[\]])", r"\\\1", str(path))


def cleanup_snapshots(api, agent_id, report=None):
    """Called under TRUENAS_LOCK, including before jobs after an interrupted run."""
    pending = list(truenas_snapshots.all())
    if not pending:
        return
    with api.session():
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
            # Snapshot preparation can exceed the login rate limit if every call
            # authenticates separately. Close this session before long Restic runs.
            with api.session():
                api.version()
                cleanup_snapshots(api, self.agent.identifier, report)
                if truenas_snapshots.count():
                    raise TrueNASError("Clean up pending snapshots before starting another TrueNAS backup")
                available = {item["id"]: item for item in api.datasets()}
                selected = backup_selection(config, available)
                host_id = api.call("system.host_id")
                name = f"drastic-{report.uuid}"
                paths = []
                report.set_data("backup_items_total", len(selected))
                # ponytail: ZFS logical sizes are estimates (metadata, sparse files, exclusions);
                # refine from Restic's existing scan instead of adding a filesystem walk.
                report.set_backup_size_estimates({f"dataset:{dataset['id']}": dataset.get("bytes_estimated")
                                                 for dataset, _, _ in selected})
                report.set_data("truenas_progress", {"phase": "snapshots", "datasets_total": len(selected)})
                for dataset, included, excluded in selected:
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
                    paths.append((dataset, api.snapshot_path(dataset, name), included, excluded))
                    report.log_message(f"Created and mounted {snapshot_id}")

            failed = []
            for index, (dataset, path, included, excluded) in enumerate(paths, 1):
                self._check_cancelled(report)
                report.set_data("truenas_progress", {"phase": "backup", "dataset": dataset["id"],
                                                    "dataset_index": index, "datasets_total": len(paths)})
                artifact = self.start_artifact(f"dataset:{dataset['id']}", report=report, data={
                    "dataset": dataset["id"], "mountpoint": dataset["mountpoint"], "zfs_snapshot": f"{dataset['id']}@{name}",
                })
                try:
                    for relative in included:
                        target = path / relative
                        # Never traverse a symlink out of the verified snapshot into live data.
                        if not target.parent.resolve().is_relative_to(path.resolve()) and relative != ".":
                            raise TrueNASError(f"Selected path escapes the snapshot: {relative}")
                        if not target.exists() and not target.is_symlink():
                            raise TrueNASError(f"Selected path is missing from the snapshot: {relative}")
                    with self.agent.resticapi.operation_cancellation(report.cancel_event):
                        previous = self.agent.resticapi.snapshots(tags=[f"job_uuid:{self.job['uuid']},dataset:{dataset['id']}"])
                        parent = max(previous, key=lambda item: datetime.fromisoformat(item["time"]))["id"] if previous else None
                        report.log_message(f"Dataset {dataset['id']}: Restic parent {parent}" if parent else
                                           f"Dataset {dataset['id']}: no matching Restic parent; reading all files")
                        status = self.agent.resticapi.backup(
                            paths=["." if relative == "." else f"./{relative}" for relative in included], cwd=str(path), parent=parent,
                            force=parent is None,
                            exclude_patterns=[literal_pattern(path / relative) for relative in excluded]
                            + [f"{literal_pattern(path)}/{pattern.lstrip('/')}" for pattern in [".zfs", *config["exclude_patterns"]]],
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
                    report.log_message(f"Dataset {dataset['id']} failed: {exc}", final_state=AgentOperationState.failed)
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
