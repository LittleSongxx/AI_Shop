from __future__ import annotations

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ALERTS = ROOT / "deploy/grafana/provisioning/alerting/aishop-alerts.yml"
COMPOSE = ROOT / "deploy/docker-compose.observability.yml"
DASHBOARD = ROOT / "deploy/grafana/dashboards/aishop-overview.json"
START_SCRIPT = ROOT / "start.sh"
BACKEND = ROOT / "AI_Shop-backend"

JAVA_APPLICATIONS = (
    BACKEND / "AI_Shop-gateway/src/main/resources/application.yml",
    BACKEND / "AI_Shop-admin/src/main/resources/application.yml",
    BACKEND / "AI_Shop-user/app/src/main/resources/application.yml",
    BACKEND / "AI_Shop-product/app/src/main/resources/application.yml",
    BACKEND / "AI_Shop-stock/app/src/main/resources/application.yml",
    BACKEND / "AI_Shop-cart/app/src/main/resources/application.yml",
    BACKEND / "AI_Shop-order/app/src/main/resources/application.yml",
    BACKEND / "AI_Shop-pay/app/src/main/resources/application.yml",
    BACKEND / "AI_Shop-coupon/app/src/main/resources/application.yml",
    BACKEND / "AI_Shop-search/src/main/resources/application.yml",
)


def _rules() -> dict[str, dict]:
    document = yaml.safe_load(ALERTS.read_text(encoding="utf-8"))
    return {
        rule["uid"]: rule
        for group in document["groups"]
        for rule in group["rules"]
    }


def _promql(rule: dict) -> str:
    query = next(item for item in rule["data"] if item["refId"] == "QUERY")
    return str(query["model"]["expr"])


def test_alert_rules_have_explicit_no_data_and_error_semantics() -> None:
    rules = _rules()

    assert rules["aishop-target-down"]["noDataState"] == "Alerting"
    assert rules["aishop-target-down"]["execErrState"] == "Alerting"
    for uid, rule in rules.items():
        assert "noDataState" in rule, uid
        assert "execErrState" in rule, uid

    rendered = ALERTS.read_text(encoding="utf-8")
    assert "humanizePercentage $values.QUERY.Value" not in rendered


def test_alert_queries_match_runtime_metric_labels() -> None:
    rules = _rules()
    http_query = _promql(rules["aishop-http-5xx"])
    backlog_query = _promql(rules["aishop-task-backlog"])

    assert 'job="aishop-java"' in http_query
    assert 'job=~"aishop-gateway|aishop-services"' not in http_query
    assert 'uri!~"/actuator/.*"' in http_query
    assert 'agent_task_backlog{job="aishop-agent"}' in backlog_query

    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    expressions = [
        target.get("expr", "")
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
    ]
    assert 'max(agent_task_backlog{job=~"aishop-agent(-worker)?"})' in expressions


def test_observability_stack_does_not_install_plugins_at_runtime() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    environment = compose["services"]["grafana"]["environment"]

    assert environment["GF_PLUGINS_PREINSTALL_DISABLED"] == "true"
    assert environment["GF_PLUGINS_PREINSTALL_AUTO_UPDATE"] == "false"
    assert environment["GF_PLUGINS_PLUGIN_ADMIN_ENABLED"] == "false"
    assert environment["GF_ANALYTICS_CHECK_FOR_UPDATES"] == "false"
    assert environment["GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES"] == "false"


def test_every_long_running_observability_service_has_a_memory_limit() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))

    for service in (
        "tempo",
        "otel-collector",
        "loki",
        "alloy",
        "prometheus",
        "grafana",
    ):
        assert "mem_limit" in compose["services"][service], service


def test_observability_host_ports_are_runtime_configurable() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")

    for variable in (
        "PROMETHEUS_PORT",
        "GRAFANA_PORT",
        "TEMPO_PORT",
        "LOKI_PORT",
        "ALLOY_PORT",
        "OTEL_GRPC_PORT",
        "OTEL_HTTP_PORT",
    ):
        assert f"${{{variable}:-" in compose


def test_full_start_manages_and_health_checks_observability_stack() -> None:
    script = START_SCRIPT.read_text(encoding="utf-8")

    assert "start_observability()" in script
    assert "normalize_boolean_setting OBSERVABILITY_ENABLED true" in script
    assert 'docker compose --env-file "$GRAFANA_ENV"' in script
    assert 'wait_http "http://127.0.0.1:$PROMETHEUS_PORT/-/ready"' in script
    assert 'wait_http "http://127.0.0.1:$GRAFANA_PORT/api/health"' in script


def test_startup_caches_project_container_port_mappings() -> None:
    script = START_SCRIPT.read_text(encoding="utf-8")

    assert "load_project_container_ports()" in script
    assert "PROJECT_CONTAINER_PORTS_LOADED=false" in script
    assert 'PROJECT_CONTAINER_PORTS["$host_port"]=1' in script


def test_every_java_service_exposes_prometheus_without_health_details() -> None:
    for path in JAVA_APPLICATIONS:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        exposed = (
            config.get("management", {})
            .get("endpoints", {})
            .get("web", {})
            .get("exposure", {})
            .get("include", "")
        )
        assert "prometheus" in {part.strip() for part in exposed.split(",")}, path
        show_details = (
            config.get("management", {})
            .get("endpoint", {})
            .get("health", {})
            .get("show-details")
        )
        assert show_details != "always", path


def test_java_launcher_isolates_spring_debug_from_host_environment() -> None:
    script = START_SCRIPT.read_text(encoding="utf-8")

    assert "normalize_boolean_setting AISHOP_SPRING_DEBUG false" in script
    assert script.count('"--debug=$AISHOP_SPRING_DEBUG"') == 2


def test_production_startup_never_deletes_search_indexes_implicitly() -> None:
    script = START_SCRIPT.read_text(encoding="utf-8")

    assert "assert_destructive_index_reset_allowed()" in script
    assert '"${APP_ENV,,}" == "production"' in script
    assert '"${AISHOP_PRODUCTION_READY,,}" == "true"' in script
    assert script.count("assert_destructive_index_reset_allowed ") >= 3
