"""Command admission, idempotent dispatch and terminal worker failures."""

import logging
from datetime import datetime, timezone

from drastic_agent.agent.enums import AgentReportState, AgentReportType
from drastic_agent.agent.report import AgentReport
from drastic_common import diagnostics
from drastic_common.agent.commands import ASYNC_AGENT_COMMANDS, AgentCommandName


def operation_type(command):
    return {
        AgentCommandName.run_job.value: AgentReportType.backup,
        AgentCommandName.run_restore.value: AgentReportType.restore,
        AgentCommandName.check_repository.value: AgentReportType.repository_check,
        AgentCommandName.unlock_repository.value: AgentReportType.repository_unlock,
    }[command]


def operation_report(command, args):
    uuid = args.get("operation_uuid") or args.get("report_uuid")
    if command == AgentCommandName.run_job.value:
        return AgentReport.backup_operation(job_id=args.get("job_id"), repository_id=args.get("repository_id"),
                                            retention_id=args.get("retention_id"), operation_uuid=uuid)
    if command == AgentCommandName.run_restore.value:
        return AgentReport.restore_operation(operation_uuid=uuid, job_id=args.get("job_id"),
                                             repository_id=args.get("repository_id"))
    if command == AgentCommandName.check_repository.value:
        return AgentReport.check_operation(repository_id=args.get("repository_id"), operation_uuid=uuid)
    return AgentReport(type=AgentReportType.repository_unlock, repository_id=args.get("repository_id"), operation_uuid=uuid)


def finish_worker_failure(command, args, error, operations):
    uuid = args.get("operation_uuid") or args.get("report_uuid")
    if any(report.uuid == uuid for report in AgentReport.finished_reports):
        return
    history = operations.find_one(uuid=uuid)
    if history and history.get("state") != AgentReportState.running.name:
        return
    report = AgentReport.get_report(uuid=uuid)
    if report is None:
        report = operation_report(command, args)
    report.log_message(f"Unexpected worker failure while executing {command}: {error}",
                       final_state=AgentReportState.failed)
    report.finish()


def submit(command, args, *, execute, manager, operations):
    """Reserve history before submitting so retries cannot admit the same UUID twice."""
    uuid = args.get("operation_uuid") or args.get("report_uuid")
    reservation = None
    created = False
    if uuid:
        reservation, created = AgentReport.reserve_history_operation(
            type=operation_type(command), operation_uuid=uuid, job_id=args.get("job_id"),
            repository_id=args.get("repository_id"), retention_id=args.get("retention_id"),
        )

    def runner():
        try:
            result = execute(**args)
            if (uuid and getattr(result, "type", None) == AgentReportType.command
                    and getattr(result, "final_state", None) == AgentReportState.failed):
                finish_worker_failure(command, args, getattr(result, "log", None) or "command returned failure", operations)
        except Exception as exc:
            logging.exception("Async agent command %s failed", command)
            if uuid:
                finish_worker_failure(command, args, exc, operations)

    resources = set()
    for key, kind in (("job_id", "job"), ("repository_id", "repository")):
        if args.get(key) is not None:
            resources.add((kind, args[key]))
    if command == AgentCommandName.run_restore.value and args.get("mode") == "proxmox_vm":
        resources.add(("proxmox-vmid", args.get("vmid")))
    future = manager.submit(runner, resources=resources, operation_uuid=uuid)
    if future is None and created:
        AgentReport.discard_history_reservation(reservation)
    return future


def validate_job(args, *, jobs, repositories, retentions):
    options = args.get("run_options") or {}
    if options.get("chain_run_id"):
        try:
            deadline = datetime.fromisoformat(options["start_deadline"])
            if deadline.tzinfo is None or deadline <= datetime.now(timezone.utc):
                return "Chain step start deadline expired"
        except (KeyError, TypeError, ValueError):
            return "Invalid chain step start deadline"
        retention_id = args.get("retention_id")
        if retention_id is not None and not retentions.find_one(id=retention_id):
            return f"Retention policy {retention_id} is not available in local sync data"
    try:
        job = jobs.find_one(id=args.get("job_id"))
        repository = repositories.find_one(id=args.get("repository_id"))
    except Exception as exc:
        return f"Local synced job data could not be validated: {exc}"
    if not job:
        return f"Job with id {args.get('job_id')} is not available in local sync data"
    if not repository:
        return f"Repository with id {args.get('repository_id')} is not available in local sync data"
    return None


def execute(request, *, find_handler, submit_async, validate_job, operations):
    """Called under the admission fence for asynchronous commands and status reads."""
    command = request["command"]
    name = command.value
    args = dict(request.get("args") or {})
    diagnostics.record("command.received", {
        "command": name,
        "arguments": {key: value for key, value in args.items() if key in {
            "job_id", "repository_id", "retention_id", "operation_uuid", "snapshot_id", "paths",
            "target", "target_vmid", "target_storage", "run_options", "action", "read_data_subset",
        }},
    }, operation_uuid=args.get("operation_uuid"))
    logging.info("Executing command %s", name)
    handler = find_handler(name)
    if handler is None:
        report = AgentReport.command_report()
        report.log_message(f"Command {name} not supported", final_state=AgentReportState.failed)
        return report.finish()
    if command not in ASYNC_AGENT_COMMANDS:
        return handler(**args)

    uuid = args.get("operation_uuid") or args.get("report_uuid")
    if not uuid:
        report = AgentReport.command_report()
        report.log_message(f"Could not start {name}: operation_uuid is required", final_state=AgentReportState.failed)
        return report.finish()
    existing = operations.find_one(uuid=uuid)
    if existing:
        report = AgentReport.command_report()
        if (existing.get("type") != operation_type(name).name or existing.get("job_id") != args.get("job_id")
                or existing.get("repository_id") != args.get("repository_id")):
            report.log_message("Operation UUID belongs to another request", final_state=AgentReportState.failed)
        else:
            report.data = {"known": True, "operation_state": existing.get("state")}
            report.log_message(f"Operation {uuid} already admitted; not executing twice")
        return report.finish()
    if command == AgentCommandName.run_job:
        reason = validate_job(args)
        if reason:
            report = AgentReport.command_report()
            report.log_message(f"Could not start run_job: {reason}", final_state=AgentReportState.failed)
            return report.finish()
    future = submit_async(name, args)
    report = AgentReport.command_report()
    if future is None:
        report.log_message(f"Could not start {name}: execution capacity or requested resource is busy",
                           final_state=AgentReportState.failed)
    elif command == AgentCommandName.run_job:
        report.log_message(f"Started job {args.get('job_id')} on repository {args.get('repository_id')}")
    elif command == AgentCommandName.run_restore:
        report.log_message(f"Started restore operation {uuid}")
    elif command == AgentCommandName.unlock_repository:
        report.log_message(f"Started repository unlock on repository {args.get('repository_id')}")
    else:
        report.log_message(f"Started repository check on repository {args.get('repository_id')}")
    return report.finish()
