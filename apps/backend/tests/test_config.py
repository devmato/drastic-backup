import pytest

from drastic_server.config import DefaultConfig, apply_environment_config, validate_config


def test_environment_overrides_code_defaults():
    config = {
        "APP_MASTER_SECRET": "default-secret",
        "SQLALCHEMY_DATABASE_URI": "sqlite:///default.db",
        "JWT_COOKIE_SECURE": DefaultConfig.JWT_COOKIE_SECURE,
    }

    apply_environment_config(
        config,
        environ={"DRASTIC_ENV": "test", "DRASTIC_JWT_COOKIE_SECURE": "true"},
    )

    assert config["DRASTIC_ENV"] == "test"
    assert config["JWT_COOKIE_SECURE"] is True


def test_required_configuration_is_validated():
    with pytest.raises(RuntimeError, match="DRASTIC_APP_MASTER_SECRET"):
        validate_config({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
