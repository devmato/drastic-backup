import drastic_agent.agent.agent as agent_module
from drastic_agent.agent.agent import Agent
from drastic_agent.agent.enums import AgentReportState, AgentReportType
from drastic_agent.agent.exceptions import AgentExeption
from drastic_agent.agent.report import AgentReport


class FakeTable:
    def __init__(self, row):
        self.row = row

    def find_one(self, **kwargs):
        return self.row


class FakeResticApi:
    def __init__(self, result=None):
        self.result = result or []
        self.calls = []

    def forget(self, **kwargs):
        self.calls.append(kwargs)
        return self.result

    def forget_snapshots(self, snapshot_ids, prune=True):
        self.calls.append({"snapshot_ids": snapshot_ids, "prune": prune})
        return self.result


class FakeRunsTable:
    def __init__(self):
        self.rows = [
            {"id": 1, "uuid": "operation-new", "type": "backup", "job_id": 7, "repository_id": 1, "state": "success", "ended": "2026-05-10T03:00:00"},
            {"id": 2, "uuid": "operation-old", "type": "backup", "job_id": 7, "repository_id": 1, "state": "success", "ended": "2026-05-10T02:00:00"},
        ]

    def find(self, **kwargs):
        return [
            row
            for row in self.rows
            if all(row.get(key) == value for key, value in kwargs.items())
        ]


class FakeArtifactsTable:
    def __init__(self):
        self.rows = [
            {"id": 1, "uuid": "artifact-new", "operation_id": 1, "snapshot_id": "snap-new", "forgotten_at": None},
            {"id": 2, "uuid": "artifact-old", "operation_id": 2, "snapshot_id": "snap-old", "forgotten_at": None},
        ]

    def find(self, **kwargs):
        return [
            row
            for row in self.rows
            if all(row.get(key) == value for key, value in kwargs.items())
        ]

    def update(self, row, keys):
        for index, existing in enumerate(self.rows):
            if all(existing[key] == row[key] for key in keys):
                self.rows[index] = dict(row)
                return


def test_cmd_run_retention_prunes_old_operation_artifacts(monkeypatch):
    agent = Agent.__new__(Agent)
    resticapi = FakeResticApi(result=[{"remove": [{"id": "snap-1"}]}])
    artifact_table = FakeArtifactsTable()

    monkeypatch.setattr(
        agent_module,
        "retentions",
        FakeTable(
            {
                "id": 2,
                "name": "Daily",
                "keep_last": 1,
                "keep_hourly": None,
                "keep_weekly": None,
                "keep_monthly": None,
                "keep_yearly": None,
            }
        ),
    )
    monkeypatch.setattr(agent_module, "agent_operations", FakeRunsTable())
    monkeypatch.setattr(agent_module, "agent_operation_artifacts", artifact_table)
    agent._Agent__resticapi = resticapi
    agent._Agent__set_repository = lambda repository_id: {"id": repository_id, "location": "/repo"}

    report = Agent.cmd_run_retention(agent, repository_id=1, retention_id=2, job_id=7)

    assert resticapi.calls == [{"snapshot_ids": ["snap-old"], "prune": True}]
    assert report.data["kept_operation_ids"] == [1]
    assert report.data["pruned_operation_ids"] == [2]
    assert report.data["snapshot_ids"] == ["snap-old"]
    assert artifact_table.rows[1]["forgotten_at"] is not None
    assert report.final_state == AgentReportState.success
    assert any("Removed 1 snapshots: snap-old" in line for line in report.log_list)


def test_cmd_get_repository_stats_returns_after_repository_error(monkeypatch):
    agent = Agent.__new__(Agent)

    class UnexpectedResticApi:
        def stats(self):
            raise AssertionError("stats should not be called")

        def cat_config(self):
            raise AssertionError("cat_config should not be called")

    agent._Agent__resticapi = UnexpectedResticApi()

    def raise_missing(repository_id):
        raise AgentExeption(f"Repository with id {repository_id} not found")

    agent._Agent__set_repository = raise_missing

    report = Agent.cmd_get_repository_stats(agent, repository_id=99)

    assert report.final_state == AgentReportState.failed
    assert any("Repository with id 99 not found" in line for line in report.log_list)


def test_operation_queue_uses_agent_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DRASTIC_AGENT_DATA_DIR", str(tmp_path))
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()

    AgentReport.job_report(job_id=1, repository_id=2)
    AgentReport.save_queue()

    assert (tmp_path / "operations").exists()


def test_cmd_check_repository_stores_check_report():
    agent = Agent.__new__(Agent)
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()

    class FakeResticApi:
        def __init__(self):
            self.calls = []

        def check(self, **kwargs):
            self.calls.append(kwargs)
            return {"message_type": "summary", "errors": 0}

    resticapi = FakeResticApi()
    agent._Agent__resticapi = resticapi
    agent._Agent__set_repository = lambda repository_id: {"id": repository_id, "location": "/repo"}

    report = Agent.cmd_check_repository(agent, repository_id=5, read_data_subset="1/10")

    assert report.type == AgentReportType.repository_check
    assert report.repository_id == 5
    assert report.final_state == AgentReportState.success
    assert report.data["read_data"] == "1/10"
    assert report.data["check"] == {"message_type": "summary", "errors": 0}
    assert resticapi.calls == [{"read_data": False, "read_data_subset": "1/10"}]
