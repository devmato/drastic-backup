"""Bridge the independent installer lifecycle to execution admission and reports."""

import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from drastic_agent.agent.enums import AgentReportState, AgentReportType
from drastic_agent.agent.report import AgentReport


def check_state(install_type, manager, *, startup, install_root, unit):
    if not manager.maintenance and not startup:
        return False
    if install_type == "docker":
        updating = (install_root / "update-request.json").exists()
        if startup and updating:
            manager.start_maintenance(lambda: None)
        elif not updating and manager.maintenance:
            manager.finish_maintenance()
            logging.info("Container update finished; resuming execution. Details: container logs")
        return manager.maintenance
    try:
        result = subprocess.run(["systemctl", "is-active", unit],
                                capture_output=True, text=True, timeout=5, check=False)
        state = result.stdout.strip()
        if startup and state in {"active", "activating", "reloading", "deactivating"}:
            manager.start_maintenance(lambda: None)
        elif state in {"inactive", "failed", "unknown"} and manager.maintenance:
            manager.finish_maintenance()
            logging.info("Update unit finished; resuming execution. Details: journalctl -u %s", unit)
    except (OSError, subprocess.SubprocessError) as exc:
        logging.warning("Could not check update unit: %s", exc)
    return manager.maintenance


def read_reports(data_dir, install_root, operations):
    import fcntl

    paths = list(Path(data_dir).glob("update-*.json"))
    if not paths:
        return
    # The installer lock distinguishes a slow update from a host/container restart.
    descriptor = os.open(install_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        updating = False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            updating = True
        for path in paths:
            try:
                status = json.loads(path.read_text())
                uuid = status["uuid"]
                row = operations.find_one(uuid=uuid)
                if row and row["state"] != "running":
                    path.unlink(missing_ok=True)
                    continue
                report = AgentReport.get_report(type=AgentReportType.agent_update, uuid=uuid)
                if report is None:
                    AgentReport.reserve_history_operation(
                        type=AgentReportType.agent_update, operation_uuid=uuid,
                        started=datetime.fromisoformat(status["started"]),
                    )
                    report = AgentReport(type=AgentReportType.agent_update, operation_uuid=uuid)
                    AgentReport.pending_reports.append(report)
                with report._lock:
                    for log in status["logs"][len(report._logs):]:
                        report.log_message(log["message"], final_state=AgentReportState.failed if log["level"] == "error" else None)
                        report._logs[-1]["created"] = datetime.fromisoformat(log["created"])
                    state = AgentReportState[status["state"]]
                    if state == AgentReportState.running and not updating:
                        report.log_message("Agent update interrupted before completion", final_state=AgentReportState.failed)
                    elif state != AgentReportState.running:
                        report.final_state = state
                    else:
                        continue
                    report.finish(ended=datetime.fromisoformat(status["ended"]) if status.get("ended") else None)
                path.unlink(missing_ok=True)
            except (OSError, ValueError, KeyError, TypeError, SQLAlchemyError):
                logging.exception("Could not read agent update status %s", path)
    finally:
        os.close(descriptor)


def start(install_type, manager, *, data_dir, install_root, command, unit):
    report = AgentReport.command_report()
    if install_type not in {"git", "docker"} or (
        install_type == "git" and Path(data_dir).resolve() != install_root / "data"
    ):
        report.log_message("Updates require a managed Linux installation running as root", final_state=AgentReportState.failed)
        return report.finish()

    def launch():
        args = [str(command), "update"]
        if install_type != "docker":
            args = ["systemd-run", "--collect", f"--unit={unit}", "--", *args]
        subprocess.run(args, check=True, capture_output=True, text=True, timeout=10)

    def preflight():
        subprocess.run([str(command), "status"], check=True, capture_output=True, text=True, timeout=10)

    try:
        # Admission stays blocked if launching times out: the updater may be running.
        if not manager.start_maintenance(launch, preflight=preflight):
            report.log_message("Cannot update: execution capacity is busy or an update is already running",
                               final_state=AgentReportState.failed)
        else:
            report.log_message("Agent update started; progress and result are available in Operations")
    except (OSError, subprocess.SubprocessError) as exc:
        report.log_message(f"Could not start agent update: {exc}", final_state=AgentReportState.failed)
    return report.finish()
