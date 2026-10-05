import json
from unittest.mock import Mock

from drastic_common import diagnostics


def test_recording_requires_live_consent_and_bounds_and_redacts_output(monkeypatch):
    report = Mock()
    diagnostics.configure()
    diagnostics.record("ignored", {})
    report.log_message.assert_not_called()
    now = [100.0]
    monkeypatch.setattr(diagnostics, "monotonic", lambda: now[0])
    diagnostics.configure(report)
    diagnostics.remember_secrets({"RESTIC_PASSWORD": "known-credential"})
    diagnostics.record("output", {"message": "Failed known-credential https://user:pass@host/path?key=abc",
                                  "nested": {"api_key": "hidden", "token_secret": "hidden"}})
    encoded = json.dumps(report.log_message.call_args.kwargs["data"])
    assert "known-credential" not in encoded and "user:pass" not in encoded and "key=abc" not in encoded
    assert "hidden" not in encoded
    diagnostics.record("large", {"data": "x" * 20000})
    assert report.log_message.call_args.kwargs["data"]["payload"]["truncated"] is True
    bounded = diagnostics.bounded

    def delayed(value):
        now[0] += 61
        return bounded(value)

    monkeypatch.setattr(diagnostics, "bounded", delayed)
    diagnostics.record("expires_during_serialization", {})
    diagnostics.record("expired", {})
    assert not diagnostics.active()
    assert report.log_message.call_count == 2
    diagnostics.configure(report, valid_for=-1)
    assert not diagnostics.active()  # A delayed grant must not extend expired consent.
    diagnostics.configure()


def test_system_sampling_tolerates_platform_without_load_average(monkeypatch):
    monkeypatch.delattr(diagnostics.os, "getloadavg", raising=False)
    assert diagnostics.system_snapshot()["scope"] == "host"
