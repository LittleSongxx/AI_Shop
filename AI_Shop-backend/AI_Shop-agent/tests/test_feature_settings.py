import pytest

from app.config.settings import Settings


def test_multi_agent_and_data_analyst_are_enabled_by_default(monkeypatch):
    monkeypatch.delenv("MULTI_AGENT_ENABLED", raising=False)
    monkeypatch.delenv("DATA_ANALYST_ENABLED", raising=False)

    settings = Settings(_env_file=None)

    assert settings.multi_agent_enabled is True
    assert settings.data_analyst_enabled is True


def test_enabled_data_analyst_requires_dedicated_credentials():
    settings = Settings(
        _env_file=None,
        data_analyst_enabled=True,
        analytics_mysql_user="",
        analytics_mysql_password="",
    )

    with pytest.raises(ValueError, match="ANALYTICS_MYSQL_USER"):
        settings.validate_runtime()


@pytest.mark.parametrize("analytics_user", ["root", "business_user"])
def test_data_analyst_rejects_root_or_business_identity(analytics_user):
    settings = Settings(
        _env_file=None,
        data_analyst_enabled=True,
        mysql_user="business_user",
        analytics_mysql_user=analytics_user,
        analytics_mysql_password="separate-secret",
    )

    with pytest.raises(ValueError, match="dedicated read-only"):
        settings.validate_runtime()


def test_data_analyst_accepts_separate_view_reader():
    settings = Settings(
        _env_file=None,
        data_analyst_enabled=True,
        mysql_user="business_user",
        analytics_mysql_user="analytics_reader",
        analytics_mysql_password="separate-secret",
        analytics_mysql_database="aishop_admin",
    )

    settings.validate_runtime()


def test_explicitly_disabled_data_analyst_does_not_require_reader_credentials():
    settings = Settings(
        _env_file=None,
        data_analyst_enabled=False,
        analytics_mysql_user="",
        analytics_mysql_password="",
    )

    settings.validate_runtime()
