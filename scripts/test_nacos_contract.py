from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy/docker-compose.middleware.yml"
ENV_TEMPLATE = ROOT / "deploy/env.production.example"
START_SCRIPT = ROOT / "start.sh"
COMMON = ROOT / "AI_Shop-backend/AI_Shop-common/src/main/resources/aishop-common.yml"
APPLICATION_CONFIGS = (
    ROOT / "AI_Shop-backend/AI_Shop-gateway/src/main/resources/application.yml",
    ROOT / "AI_Shop-backend/AI_Shop-admin/src/main/resources/application.yml",
    ROOT / "AI_Shop-backend/AI_Shop-search/src/main/resources/application.yml",
    *(
        ROOT
        / f"AI_Shop-backend/AI_Shop-{service}/app/src/main/resources/application.yml"
        for service in ("user", "product", "stock", "cart", "order", "pay", "coupon")
    ),
)


def test_bundled_nacos_is_the_2_x_compatibility_bridge() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))

    nacos = compose["services"]["nacos"]
    assert nacos["image"] == "nacos/nacos-server:v2.5.3"
    assert nacos["environment"]["NACOS_AUTH_ENABLE"] == "true"
    assert nacos["environment"]["MYSQL_SERVICE_USER"].startswith(
        "${NACOS_MYSQL_USER:"
    )
    assert nacos["environment"]["JVM_XMS"] == "${NACOS_JVM_XMS:-128m}"
    assert nacos["environment"]["JVM_XMX"] == "${NACOS_JVM_XMX:-256m}"


def test_java_clients_keep_the_legacy_default_namespace_explicit() -> None:
    common = yaml.safe_load(COMMON.read_text(encoding="utf-8"))
    assert common["seata"]["registry"]["nacos"]["namespace"] == "${NACOS_NAMESPACE:}"

    for path in APPLICATION_CONFIGS:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        namespace = config["spring"]["cloud"]["nacos"]["discovery"]["namespace"]
        assert namespace == "${NACOS_NAMESPACE:}", path


def test_local_launcher_persists_the_empty_namespace_contract() -> None:
    script = START_SCRIPT.read_text(encoding="utf-8")
    template = ENV_TEMPLATE.read_text(encoding="utf-8")

    assert "NACOS_NAMESPACE" in script
    assert 'export NACOS_NAMESPACE="${NACOS_NAMESPACE:-}"' in script
    assert "NACOS_NAMESPACE=\n" in template
