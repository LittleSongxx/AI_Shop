import pytest

from app.config.settings import Settings


def test_local_mysql_defaults_match_the_project_compose_stack(monkeypatch):
    for variable in (
        "MYSQL_HOST",
        "MYSQL_PORT",
        "MYSQL_USER",
        "MYSQL_PASSWORD",
        "MYSQL_DATABASE",
        "MYSQL_POOL_MIN_SIZE",
        "MYSQL_POOL_MAX_SIZE",
    ):
        monkeypatch.delenv(variable, raising=False)

    settings = Settings(_env_file=None)

    assert settings.mysql_host == "localhost"
    assert settings.mysql_port == 3306
    assert settings.mysql_user == "aishop"
    assert settings.mysql_password == ""
    assert settings.mysql_database == "aishop_agent"
    assert settings.mysql_pool_min_size == 1
    assert settings.mysql_pool_max_size == 8


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [(0, 8), (8, 4), (1, 65)],
)
def test_mysql_pool_bounds_are_validated(minimum, maximum):
    with pytest.raises(ValueError, match="MYSQL_POOL_MIN_SIZE"):
        Settings(
            _env_file=None,
            mysql_pool_min_size=minimum,
            mysql_pool_max_size=maximum,
        )


def test_mysql_dsn_encodes_credentials_and_ipv6():
    settings = Settings(
        _env_file=None,
        mysql_host="2001:db8::8",
        mysql_user="shop@api",
        mysql_password="p@ss:/?#[]",
    )

    assert settings.mysql_dsn == (
        "mysql+aiomysql://shop%40api:p%40ss%3A%2F%3F%23%5B%5D"
        "@[2001:db8::8]:3306/aishop_agent"
    )


def test_multi_agent_and_data_analyst_are_enabled_by_default(monkeypatch):
    monkeypatch.delenv("MULTI_AGENT_ENABLED", raising=False)
    monkeypatch.delenv("DATA_ANALYST_ENABLED", raising=False)

    settings = Settings(_env_file=None)

    assert settings.multi_agent_enabled is True
    assert settings.orchestration_mode == "adaptive"
    assert settings.data_analyst_enabled is True
    assert settings.multi_agent_specialist_timeout_seconds == 12
    assert settings.llm_timeout == 20
    assert settings.llm_max_retries == 1
    assert settings.agent_llm_call_deadline_seconds == 25.0
    assert settings.agent_budget_max_tokens == 16_000
    assert settings.agent_budget_max_steps == 16
    assert settings.agent_budget_deadline_seconds == 60.0
    assert settings.intent_llm_timeout_seconds == 8
    assert settings.graph_max_react_rounds == 3


@pytest.mark.parametrize("deadline", [4, 121])
def test_agent_llm_call_deadline_is_bounded(deadline):
    with pytest.raises(ValueError, match="AGENT_LLM_CALL_DEADLINE_SECONDS"):
        Settings(_env_file=None, agent_llm_call_deadline_seconds=deadline)


def test_agent_llm_call_deadline_leaves_time_for_controlled_termination():
    with pytest.raises(ValueError, match="below both Agent and Worker deadlines"):
        Settings(
            _env_file=None,
            agent_llm_call_deadline_seconds=60,
            agent_budget_deadline_seconds=60,
            agent_task_deadline_seconds=120,
        )


@pytest.mark.parametrize("timeout", [2, 31])
def test_multi_agent_specialist_timeout_is_bounded(timeout):
    with pytest.raises(ValueError, match="MULTI_AGENT_SPECIALIST_TIMEOUT_SECONDS"):
        Settings(_env_file=None, multi_agent_specialist_timeout_seconds=timeout)


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


def test_production_visual_search_requires_provider_credentials():
    settings = Settings(
        _env_file=None,
        app_env="production",
        visual_search_enabled=True,
        visual_api_key="",
    )

    with pytest.raises(ValueError, match="VISUAL_API_KEY"):
        settings.validate_runtime()


def test_redis_url_encodes_acl_credentials_and_supports_tls_ipv6():
    settings = Settings(
        _env_file=None,
        redis_host="2001:db8::7",
        redis_port=6380,
        redis_db=3,
        redis_username="aishop@ops",
        redis_password="p@ss:/?#[]",
        redis_ssl_enabled=True,
    )

    assert settings.redis_url == (
        "rediss://aishop%40ops:p%40ss%3A%2F%3F%23%5B%5D@[2001:db8::7]:6380/3"
    )


def test_redis_url_keeps_legacy_no_auth_shape():
    settings = Settings(
        _env_file=None,
        redis_host="127.0.0.1",
        redis_port=6380,
        redis_db=0,
    )

    assert settings.redis_url == "redis://127.0.0.1:6380/0"


def test_production_requires_redis_authentication():
    settings = Settings(
        _env_file=None,
        app_env="production",
        redis_password="",
        visual_search_enabled=False,
    )

    with pytest.raises(ValueError, match="REDIS_PASSWORD"):
        settings.validate_runtime()


@pytest.mark.parametrize(
    ("mysql_user", "mysql_password", "message"),
    [
        ("root", "secret", "must not use"),
        ("aishop", "", "MYSQL_USER and MYSQL_PASSWORD"),
    ],
)
def test_production_requires_a_non_root_mysql_identity(
    mysql_user, mysql_password, message
):
    settings = Settings(
        _env_file=None,
        app_env="production",
        mysql_user=mysql_user,
        mysql_password=mysql_password,
        visual_search_enabled=False,
    )

    with pytest.raises(ValueError, match=message):
        settings.validate_runtime()


def test_elasticsearch_basic_auth_must_be_configured_as_a_pair():
    with pytest.raises(ValueError, match="ES_USERNAME and ES_PASSWORD"):
        Settings(_env_file=None, es_username="aishop-search", es_password="")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {
                "es_hosts": "http://elasticsearch.internal:9200",
                "es_username": "aishop-search",
                "es_password": "secret",
            },
            "ES_HOSTS must use HTTPS",
        ),
        (
            {"es_hosts": "https://elasticsearch.internal:9200"},
            "ES_USERNAME and ES_PASSWORD",
        ),
        (
            {
                "es_hosts": "https://elasticsearch.internal:9200",
                "es_username": "aishop-search",
                "es_password": "secret",
                "es_verify_ssl": False,
            },
            "ES_VERIFY_SSL",
        ),
    ],
)
def test_production_requires_authenticated_verified_elasticsearch(overrides, message):
    settings = Settings(
        _env_file=None,
        app_env="production",
        visual_search_enabled=False,
        **overrides,
    )

    with pytest.raises(ValueError, match=message):
        settings.validate_runtime()
