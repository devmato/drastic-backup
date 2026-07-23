from drastic_agent.agent.report import AgentReport


def setup_function():
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()


def test_restore_status_uses_restore_specific_metrics():
    report = AgentReport.restore_report(
        report_uuid="restore-report-1",
        job_id=7,
        repository_id=3,
        data={"snapshot_id": "snap-1"},
    )

    AgentReport.process_restore_status(
        '{"message_type":"summary","total_files":1,"files_restored":4,"total_bytes":14,"bytes_restored":14}',
        report_uuid="restore-report-1",
        pid=123,
    )

    assert report.data["pid"] == 123
    assert report.data["restore_files_total"] == 1
    assert report.data["restore_files_restored"] == 4
    assert report.data["restore_bytes_total"] == 14
    assert report.data["restore_bytes_restored"] == 14
    assert "files_processed" not in report.data
    assert "bytes_processed" not in report.data


def test_restore_status_removes_finished_process_pid():
    report = AgentReport.restore_report(
        report_uuid="restore-report-pid",
        job_id=7,
        repository_id=3,
    )

    AgentReport.process_restore_status(None, report_uuid=report.uuid, pid=123)
    AgentReport.process_restore_status(None, report_uuid=report.uuid, pid=None)

    assert "pid" not in report.data


def test_save_queue_marks_all_pending_reports_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("DRASTIC_AGENT_DATA_DIR", str(tmp_path))
    report_1 = AgentReport.job_report(job_id=1, repository_id=2)
    report_2 = AgentReport.job_report(job_id=3, repository_id=4)

    AgentReport.save_queue()

    assert AgentReport.pending_reports == []
    assert len(AgentReport.finished_reports) == 2
    assert report_1 in AgentReport.finished_reports
    assert report_2 in AgentReport.finished_reports
    assert all(report.final_state.name == "failed" for report in AgentReport.finished_reports)
