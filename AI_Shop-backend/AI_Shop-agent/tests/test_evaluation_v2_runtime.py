import os

from evaluation.core.runtime import load_runtime_environment


def test_runtime_env_loads_dynamic_ports_and_derived_urls(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime.env"
    runtime.write_text(
        "AGENT_PORT=7192\n"
        "AGENT_WORKER_METRICS_PORT=7195\n"
        "GATEWAY_PORT=8181\n"
        "MCP_PORT=7199\n"
        "ES_PORT=9292\n"
        "NACOS_NAMESPACE=''\n",
        encoding="utf-8",
    )
    for key in (
        "APP_PORT",
        "WORKER_METRICS_PORT",
        "AGENT_BASE_URL",
        "JAVA_WEB_URL",
        "MCP_SERVER_URL",
        "ES_HOSTS",
        "NACOS_NAMESPACE",
        "AISHOP_RUNTIME_ENV",
    ):
        monkeypatch.delenv(key, raising=False)

    metadata = load_runtime_environment(runtime)

    assert metadata["loaded"] is True
    assert os.environ["APP_PORT"] == "7192"
    assert os.environ["WORKER_METRICS_PORT"] == "7195"
    assert os.environ["AGENT_BASE_URL"] == "http://127.0.0.1:7192"
    assert os.environ["JAVA_WEB_URL"] == "http://127.0.0.1:8181"
    assert os.environ["MCP_SERVER_URL"] == "http://127.0.0.1:7199"
    assert os.environ["ES_HOSTS"] == "http://127.0.0.1:9292"
    assert os.environ["NACOS_NAMESPACE"] == ""


def test_explicit_environment_wins_over_runtime_file(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime.env"
    runtime.write_text("AGENT_PORT=7192\nGATEWAY_PORT=8181\n", encoding="utf-8")
    monkeypatch.setenv("APP_PORT", "7999")
    monkeypatch.setenv("JAVA_WEB_URL", "http://example.test:9999")

    load_runtime_environment(runtime)

    assert os.environ["APP_PORT"] == "7999"
    assert os.environ["JAVA_WEB_URL"] == "http://example.test:9999"
