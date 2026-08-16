import httpx

from app.config.settings import Settings
from app.infra import http_client


def test_elasticsearch_clients_receive_basic_auth_and_tls_verification(monkeypatch):
    monkeypatch.setattr(
        "app.config.settings.get_settings",
        lambda: Settings(
            _env_file=None,
            es_hosts="https://elasticsearch.internal:9200",
            es_username="aishop-search",
            es_password="secret",
            es_verify_ssl=True,
        ),
    )

    options = http_client._dependency_client_options("es")

    assert options["verify"] is True
    assert isinstance(options["auth"], httpx.BasicAuth)


def test_non_elasticsearch_clients_keep_their_existing_defaults():
    assert http_client._dependency_client_options("java") == {}
