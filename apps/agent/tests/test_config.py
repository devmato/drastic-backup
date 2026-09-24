from drastic_agent.config import DefaultConfig, env_flag, env_int, env_value


def test_code_defaults_and_environment_overrides(monkeypatch):
    monkeypatch.delenv("DRASTIC_TASK_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("DRASTIC_ENV", "test")
    monkeypatch.setenv("DRASTIC_PROXMOX_VERIFY_TLS", "true")

    assert env_int("DRASTIC_TASK_TIMEOUT_SECONDS", DefaultConfig.TASK_TIMEOUT_SECONDS) == 600
    assert env_value("DRASTIC_ENV", DefaultConfig.ENV) == "test"
    assert env_flag("DRASTIC_PROXMOX_VERIFY_TLS", DefaultConfig.PROXMOX_VERIFY_TLS)
