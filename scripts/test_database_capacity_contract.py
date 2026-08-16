from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
COMMON = ROOT / "AI_Shop-backend/AI_Shop-common/src/main/resources/aishop-common.yml"
AGENT_SETTINGS = ROOT / "AI_Shop-backend/AI_Shop-agent/app/config/settings.py"
AGENT_POOL = ROOT / "AI_Shop-backend/AI_Shop-agent/app/db/pool.py"
COMPOSE = ROOT / "deploy/docker-compose.middleware.yml"
PROVISION = ROOT / "deploy/provision-app-mysql-user.sh"
FLYWAY_PROVISION = ROOT / "deploy/provision-flyway-identity.sh"
INFRA_PROVISION = ROOT / "deploy/provision-infrastructure-mysql-users.sh"
RABBIT_CONFIG = ROOT / "deploy/rabbitmq/rabbitmq.conf"
START_SCRIPT = ROOT / "start.sh"


def test_java_services_use_bounded_configurable_hikari_pools() -> None:
    config = yaml.safe_load(COMMON.read_text(encoding="utf-8"))
    hikari = config["spring"]["datasource"]["hikari"]

    assert hikari["minimum-idle"] == "${DB_POOL_MIN_IDLE:1}"
    assert hikari["maximum-pool-size"] == "${DB_POOL_MAX_SIZE:4}"
    assert hikari["connection-timeout"] == "${DB_POOL_CONNECTION_TIMEOUT_MS:5000}"
    assert hikari["max-lifetime"] == "${DB_POOL_MAX_LIFETIME_MS:1800000}"


def test_agent_pool_is_bounded_and_recycles_connections() -> None:
    settings = AGENT_SETTINGS.read_text(encoding="utf-8")
    pool = AGENT_POOL.read_text(encoding="utf-8")

    assert "mysql_pool_min_size: int = 1" in settings
    assert "mysql_pool_max_size: int = 8" in settings
    assert "mysql_pool_recycle_seconds: int = 1800" in settings
    assert "minsize=settings.mysql_pool_min_size" in pool
    assert "maxsize=settings.mysql_pool_max_size" in pool
    assert "pool_recycle=settings.mysql_pool_recycle_seconds" in pool


def test_local_launcher_provisions_a_non_root_business_identity() -> None:
    launcher = START_SCRIPT.read_text(encoding="utf-8")
    provisioner = PROVISION.read_text(encoding="utf-8")

    assert 'export MYSQL_USER="${MYSQL_USER:-aishop}"' in launcher
    assert "provision_application_mysql_user" in launcher
    assert "provision-app-mysql-user.sh" in launcher
    assert "business services must not use the MySQL root identity" in provisioner
    for schema in (
        "aishop_admin",
        "aishop_agent",
        "aishop_cart",
        "aishop_coupon",
        "aishop_order",
        "aishop_pay",
        "aishop_product",
        "aishop_search",
        "aishop_stock",
        "aishop_user",
    ):
        assert (
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {schema}.*"
            in provisioner
        )
        assert f"GRANT ALL PRIVILEGES ON {schema}.*" not in provisioner


def test_schema_migrations_use_a_separate_privileged_identity() -> None:
    launcher = START_SCRIPT.read_text(encoding="utf-8")
    provisioner = FLYWAY_PROVISION.read_text(encoding="utf-8")

    assert "FLYWAY_USER FLYWAY_PASSWORD" in launcher
    assert "provision_flyway_mysql_user" in launcher
    assert "Flyway and business services must use separate" in provisioner
    assert 'MYSQL_USER="$FLYWAY_USER" MYSQL_PASSWORD="$FLYWAY_PASSWORD"' in launcher
    for schema in (
        "aishop_admin",
        "aishop_agent",
        "aishop_cart",
        "aishop_coupon",
        "aishop_order",
        "aishop_pay",
        "aishop_product",
        "aishop_search",
        "aishop_stock",
        "aishop_user",
    ):
        assert f"GRANT ALL PRIVILEGES ON {schema}.*" in provisioner


def test_middleware_versions_and_rabbit_memory_contract_are_pinned() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = compose["services"]

    assert services["redis"]["image"] == "redis:7.4.7-alpine"
    assert services["rabbitmq"]["image"] == "rabbitmq:4.2.9-management"
    assert (
        "RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS"
        not in services["rabbitmq"]["environment"]
    )
    assert (
        "./rabbitmq/rabbitmq.conf:/etc/rabbitmq/conf.d/20-aishop.conf:ro"
        in services["rabbitmq"]["volumes"]
    )
    rabbit_config = RABBIT_CONFIG.read_text(encoding="utf-8")
    assert "vm_memory_high_watermark.absolute = 460MiB" in rabbit_config
    assert "disk_free_limit.absolute = 1GiB" in rabbit_config


def test_local_mysql_capacity_defaults_leave_cgroup_headroom() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    mysql = compose["services"]["mysql"]

    assert "--max_connections=${MYSQL_MAX_CONNECTIONS:-100}" in mysql["command"]
    assert mysql["mem_limit"] == "${MYSQL_MEMORY_LIMIT:-768m}"


def test_nacos_and_seata_use_separate_schema_scoped_mysql_identities() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    provisioner = INFRA_PROVISION.read_text(encoding="utf-8")
    launcher = START_SCRIPT.read_text(encoding="utf-8")

    nacos_env = compose["services"]["nacos"]["environment"]
    seata_env = compose["services"]["seata-server"]["environment"]
    assert nacos_env["MYSQL_SERVICE_USER"].startswith("${NACOS_MYSQL_USER:")
    assert seata_env["SEATA_MYSQL_USER"].startswith("${SEATA_MYSQL_USER:")
    assert "provision_infrastructure_mysql_users" in launcher
    assert "GRANT ALL PRIVILEGES ON nacos.*" in provisioner
    assert "GRANT ALL PRIVILEGES ON seata.*" in provisioner
    assert "must not use the MySQL root identity" in provisioner
