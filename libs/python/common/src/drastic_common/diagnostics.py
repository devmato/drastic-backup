"""Opt-in, bounded diagnostics shared by the agent and its subprocess wrapper."""

import hashlib
import json
import logging
import os
import re
from functools import lru_cache
from importlib.util import find_spec
from pathlib import Path
from threading import RLock
from time import monotonic

MAX_EVENT_BYTES = 16_384
MAX_EVENTS = 256
_lock = RLock()
_until = 0.0
_report = None
_secrets = set()
_sensitive = re.compile(r"password|secret|token|api.?key|access.?key|credential|authorization|cookie|private.?key|encrypted|environment|recovery.?key", re.I)
_logger = logging.getLogger("drastic.diagnostics")
_logger.setLevel(logging.INFO)


@lru_cache(maxsize=4)
def source_fingerprint(package):
    """Identify deployed code even when development builds share a package version."""
    try:
        root = Path(find_spec(package).origin).parent
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*.py")):
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()[:16]
    except (OSError, AttributeError, TypeError):
        return None


def remember_secrets(values):
    """Register known credentials before processing any free-form command output."""
    with _lock:
        for key, value in values.items():
            if _sensitive.search(str(key)) and isinstance(value, str) and len(value) >= 4:
                _secrets.add(value)


def redact(value):
    if isinstance(value, dict):
        return {str(key): "[redacted]" if _sensitive.search(str(key)) else redact(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        with _lock:
            secrets = tuple(_secrets)
        for secret in secrets:
            value = value.replace(secret, "[redacted]")
        value = re.sub(r"((?:https?|sftp|ssh):(?://)?)[^\s/@]+@", r"\1[redacted]@", value)
        value = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", "[redacted]", value, flags=re.S)
        value = re.sub(r"(https?://[^\s?#]+)\?[^\s]+", r"\1?[redacted]", value)
        value = re.sub(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9_.+/=-]+", r"\1 [redacted]", value)
        return re.sub(r"(?i)((?:password|secret|token|api[_-]?key|authorization)\s*[:=]\s*)[^\s,;]+", r"\1[redacted]", value)
    return value


def bounded(value):
    value = redact(value)
    encoded = json.dumps(value, default=str, ensure_ascii=False).encode()
    if len(encoded) <= MAX_EVENT_BYTES:
        return json.loads(encoded)
    return {"truncated": True, "original_bytes": len(encoded),
            "preview": encoded[:MAX_EVENT_BYTES // 4].decode(errors="replace")}


def configure(report=None, valid_for=60):
    global _until, _report
    with _lock:
        _report = report
        _until = monotonic() + max(0, min(60, valid_for)) if report is not None else 0


def active():
    return monotonic() < _until


def record(event_type, payload, *, operation_uuid=None):
    report = _report
    if report is None or not active():
        return
    try:
        payload = bounded(payload)
        if active():
            local_event(event_type, payload, operation_uuid=operation_uuid)
            report.log_message(event_type, data={"operation_uuid": operation_uuid, "payload": payload})
    except Exception:
        with report._lock:
            report.set_data("dropped_events", report.data.get("dropped_events", 0) + 1)


def local_event(event_type, payload, *, operation_uuid=None):
    """Keep failures observable without acquiring the report queue's locks."""
    if active():
        try:
            _logger.info(event_type, extra={"diagnostic": bounded(payload), "operation_uuid": operation_uuid})
        except Exception:
            pass  # Diagnostic logging must not interrupt a data stream.


def system_snapshot(pids=()):
    """Linux cumulative counters, not inferred per-operation utilization."""
    result = {"scope": "host", "cpu_count": os.cpu_count(), "processes": {}, "truncated_counters": []}
    try:
        result["load_average"] = os.getloadavg()
    except (AttributeError, OSError):
        pass
    paths = {"cpu": "/proc/stat", "memory": "/proc/meminfo", "disk_io": "/proc/diskstats",
             "network_io": "/proc/net/dev", "boot_id": "/proc/sys/kernel/random/boot_id"}
    for key, path in paths.items():
        try:
            with open(path) as stream:
                raw = stream.read(32_768)
                lines = raw.splitlines()
                if key == "cpu":
                    lines = [line for line in lines if line.startswith(("cpu ", "ctxt ", "btime ", "processes ", "procs_"))]
                elif key == "memory":
                    lines = [line for line in lines if line.startswith(("MemTotal:", "MemAvailable:", "SwapTotal:", "SwapFree:", "Dirty:", "Writeback:"))]
                elif key == "disk_io":
                    lines = [line for line in lines if not line.split()[2].startswith(("loop", "ram"))]
                text = "\n".join(lines)
                if len(raw) == 32_768 or len(text) > 4096:
                    result["truncated_counters"].append(key)
                result[key] = text[:4096]
        except OSError:
            result[key] = None
    processes = list(dict.fromkeys([os.getpid(), *pids]))
    result["process_limit_reached"] = len(processes) > 32
    try:
        result["clock_ticks_per_second"] = os.sysconf("SC_CLK_TCK")
    except (AttributeError, OSError, ValueError):
        result["clock_ticks_per_second"] = None
    for pid in processes[:32]:
        if not isinstance(pid, int) or pid <= 0:
            continue
        fields = {}
        for name in ("stat", "io", "status"):
            try:
                text = (Path("/proc") / str(pid) / name).read_text()
                if name == "status":
                    text = "\n".join(line for line in text.splitlines()
                                     if line.startswith(("Name:", "State:", "VmRSS:", "Threads:")))
                fields[name] = text[:2048]
            except OSError:
                fields[name] = None
        result["processes"][str(pid)] = fields
    return result
