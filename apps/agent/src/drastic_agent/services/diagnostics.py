"""Fixed, read-only agent views that do not depend on report delivery."""

import json
import logging
import os
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import monotonic

from drastic_agent.agent.report import AgentReport
from drastic_common import diagnostics
from drastic_common.restic.client import ResticApi

LOG_NAME = "diagnostics.log"
_handler = None


class DiagnosticLog(RotatingFileHandler):
    last_error = None

    def _open(self):
        descriptor = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
        return os.fdopen(descriptor, "a", encoding="utf-8")

    def emit(self, record):
        if not diagnostics.active() or not (record.name == "root" or record.name.startswith("drastic")):
            return
        try:
            # Sanitize before the handler sees the record, including its error path.
            item = diagnostics.bounded({
                "at": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                "level": record.levelname, "logger": record.name,
                "message": logging.Formatter().format(record),
                "operation_uuid": getattr(record, "operation_uuid", None),
                "data": getattr(record, "diagnostic", None),
            })
            safe = logging.makeLogRecord({"msg": json.dumps(item, ensure_ascii=False), "args": (), "levelno": record.levelno})
            super().emit(safe)
        except Exception as exc:
            self.last_error = type(exc).__name__

    def handleError(self, record):
        self.last_error = type(sys.exc_info()[1]).__name__


def install_log(data_dir):
    global _handler
    filename = str(Path(data_dir).absolute() / LOG_NAME)
    if _handler is None or _handler.baseFilename != filename:
        if _handler is not None:
            logging.getLogger().removeHandler(_handler)
            _handler.close()
        _handler = DiagnosticLog(filename, maxBytes=1024 * 1024, backupCount=1, encoding="utf-8", delay=True)
        _handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(_handler)
    return _handler


@contextmanager
def available(lock):
    acquired = lock.acquire(timeout=0.02)
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()


def runtime(agent):
    result = {"connected": agent.connected, "recording": diagnostics.active(), "version": agent.version,
              "agent_source": diagnostics.source_fingerprint("drastic_agent"),
              "common_source": diagnostics.source_fingerprint("drastic_common"),
              "scheduler": getattr(agent, "_debug_scheduler", None),
              "report_delivery": getattr(agent, "_debug_report", None),
              "last_sync": getattr(agent, "_debug_sync", None),
              "pending_reports": len(AgentReport.pending_reports),
              "finished_reports": len(AgentReport.finished_reports)}
    manager = getattr(agent, "_Agent__execution", None)
    if manager:
        with available(manager._lock) as acquired:
            result["execution"] = ({"admitted": manager._admitted, "max_workers": manager.max_workers,
                                    "max_pending": manager.max_pending, "maintenance": manager._maintenance,
                                    "resources": sorted(map(str, manager._resources))} if acquired else {"unavailable": "lock_busy"})
    with available(AgentReport._registry_lock) as acquired:
        operations = list(AgentReport.running_operations.values()) if acquired else []
        result["operations"] = {"items": [], "truncated": len(operations) > 20, "unavailable": None if acquired else "lock_busy"}
    for operation in operations[:20]:
        with available(operation._lock) as acquired:
            result["operations"]["items"].append({"uuid": operation.uuid, **({
                "type": operation.type.name, "job_id": operation.job_id, "repository_id": operation.repository_id,
                "state": operation.state.name, "data": diagnostics.bounded(operation.data),
                "last_logs": [diagnostics.bounded(item) for item in operation._logs[-5:]],
                "sent": operation.sent, "log_cursor": operation._log_cursor,
            } if acquired else {"unavailable": "lock_busy"})})
    with available(ResticApi._process_lock) as acquired:
        managed = list(ResticApi._processes.values()) if acquired else []
        result["processes"] = [{"pids": [p.pid for p in item._processes]} for item in managed[:20]]
        if not acquired:
            result["processes_unavailable"] = "lock_busy"
    result["system"] = diagnostics.system_snapshot([pid for item in result["processes"] for pid in item["pids"]])
    result["local_log_error"] = _handler.last_error if _handler else "not_initialized"
    return result


def thread_stacks():
    # Frame locations only: no locals, arguments, source lines or arbitrary code evaluation.
    frames = sys._current_frames()
    result = []
    for thread in threading.enumerate()[:64]:
        frame = frames.get(thread.ident)
        stack = []
        while frame is not None and len(stack) < 32:
            stack.append({"file": frame.f_code.co_filename, "line": frame.f_lineno, "function": frame.f_code.co_name})
            frame = frame.f_back
        result.append({"thread": thread.name, "id": thread.ident, "stack": stack})
    return {"items": result, "thread_limit": 64, "frame_limit": 32}


def logs(data_dir, limit):
    entries = []
    truncated = False
    for suffix in (".1", ""):
        try:
            with open(Path(data_dir) / (LOG_NAME + suffix), "rb") as stream:
                size = os.fstat(stream.fileno()).st_size
                stream.seek(max(0, size - 65536))
                if stream.tell():
                    stream.readline()  # Skip a partial first JSON line.
                    truncated = True
                for line in stream.read(65536).splitlines():
                    try:
                        entries.append(json.loads(line))
                    except (ValueError, UnicodeDecodeError):
                        truncated = True
        except FileNotFoundError:
            continue
        except OSError as exc:
            return {"unavailable": type(exc).__name__}
    selected, size = [], 0
    for entry in reversed(entries):
        size += len(json.dumps(entry, ensure_ascii=False).encode())
        if len(selected) == limit or size > 48000:
            break
        selected.append(entry)
    return {"items": list(reversed(selected)), "truncated": truncated or len(selected) < len(entries)}


def snapshot(agent, data_dir, section, limit):
    started = monotonic()
    data_dir = Path(data_dir).absolute()
    if section == "runtime":
        data = runtime(agent)
    elif section == "threads":
        data = thread_stacks()
    else:
        data = logs(data_dir, limit)
    encoded = json.dumps(diagnostics.redact(data), default=str, ensure_ascii=False).encode()
    if len(encoded) > 65536:
        data = {"truncated": True, "original_bytes": len(encoded), "preview": encoded[:16000].decode(errors="replace")}
    else:
        data = json.loads(encoded)
    return {"source": "live", "section": section, "captured_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": monotonic() - started, "data": data}
