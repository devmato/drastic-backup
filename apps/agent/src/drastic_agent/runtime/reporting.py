"""Replayable report delivery; acknowledgements advance only the sent snapshot."""

import logging
from copy import deepcopy
from datetime import datetime, timezone
from time import monotonic

from drastic_agent.agent.report import AgentReport
from drastic_agent.agent.schemas import AgentReportSchema
from drastic_agent.storage.operation_store import operation_store
from drastic_common import diagnostics


def flush(send_report):
    for report in list(AgentReport.pending_reports):
        if not report.sent:
            send_report(report)

    # A child cannot overtake its parent. Rotate failed sends so others make progress.
    with AgentReport._registry_lock:
        queue = AgentReport.finished_reports
        outstanding = {report.uuid for report in queue}
        outstanding.update(report.uuid for report in AgentReport.pending_reports)
    for _ in range(len(queue)):
        with AgentReport._registry_lock:
            report = queue.popleft()
            if (report.parent_operation_uuid in outstanding
                    or report.sent and report.data.get("backup_completion_pending")):
                queue.append(report)
                continue
            # Snapshot reconciliation must still find this report during network I/O.
            AgentReport.delivering_reports[report.uuid] = report
        try:
            if send_report(report):
                operation_store.delete(report.uuid)
            else:
                with AgentReport._registry_lock:
                    queue.append(report)
        finally:
            with AgentReport._registry_lock:
                AgentReport.delivering_reports.pop(report.uuid, None)
        break


def send(report, *, request, debug, completion_pending=None):
    """Release the report lock during I/O; concurrent updates must stay unsent."""
    debug.update(operation_uuid=report.uuid, attempt_at=datetime.now(timezone.utc).isoformat(), phase="serializing")
    started = monotonic()
    try:
        with report._lock:
            defer_completion = bool(completion_pending and completion_pending())
            if hasattr(report, "diagnostic_at"):
                if diagnostics.active():
                    report.diagnostic_at = datetime.now(timezone.utc)
                else:
                    del report.diagnostic_at
            diagnostic_at = getattr(report, "diagnostic_at", None)
            if defer_completion:
                report.data.update(backup_completion_pending=True, backup_completion_failed=True)
            payload = deepcopy(AgentReportSchema().dump(report))
            if defer_completion:
                # Execution has ended, but unresolved snapshot IDs must not be published.
                payload["artifacts"] = []
            report.sent = True
        request_started = monotonic()
        debug["phase"] = "sending"
        response = request("operation", operation_json=payload)
        debug["phase"] = "processing_response"
        success = bool(response.get("success"))
        if success:
            logs = payload.get("logs") or []
            if logs:
                report.mark_logs_sent(logs[-1]["sequence"])
            if report.data.get("diagnostic"):
                enabled = response["result"]["enabled"]
                diagnostics.configure(report if enabled else None, valid_for=60 - (monotonic() - request_started))
                if not enabled:
                    report.mark_logs_sent()
            with report._lock:
                if diagnostic_at is not None and getattr(report, "diagnostic_at", None) == diagnostic_at:
                    del report.diagnostic_at
        else:
            report.sent = False
        debug.update(phase="complete", success=success, duration_seconds=monotonic() - started,
                     error=None if success else diagnostics.bounded(response.get("result")))
        if success:
            debug["last_success_at"] = datetime.now(timezone.utc).isoformat()
        else:
            diagnostics.local_event("report.rejected", debug)
        return success and not defer_completion
    except Exception as exc:
        report.sent = False
        debug.update(success=False, error=diagnostics.bounded({"type": type(exc).__name__, "message": str(exc)}),
                     duration_seconds=monotonic() - started)
        diagnostics.local_event("report.failed", debug)
        logging.debug("Failed to send report %s: %s", report.uuid, exc)
        return False
