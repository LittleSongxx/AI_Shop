from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy/docker-compose.middleware.yml"
START_SCRIPT = ROOT / "start.sh"
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


def test_every_java_service_accepts_the_complete_redis_contract() -> None:
    for path in APPLICATION_CONFIGS:
        redis = yaml.safe_load(path.read_text(encoding="utf-8"))["spring"]["data"]["redis"]
        assert redis["username"] == "${REDIS_USERNAME:}", path
        assert redis["password"] == "${REDIS_PASSWORD:}", path
        assert redis["database"] == "${REDIS_DB:0}", path
        assert redis["ssl"]["enabled"] == "${REDIS_SSL_ENABLED:false}", path


def test_local_redis_uses_the_persisted_password_for_server_and_healthcheck() -> None:
    compose_text = COMPOSE.read_text(encoding="utf-8")
    compose = yaml.safe_load(compose_text)
    redis = compose["services"]["redis"]

    assert redis["environment"]["REDIS_PASSWORD"] == "${REDIS_PASSWORD:-}"
    assert "--requirepass" in redis["command"][2]
    assert "redis-cli --no-auth-warning -a" in redis["healthcheck"]["test"][1]


def test_redis_maxmemory_leaves_cgroup_headroom_for_persistence_and_buffers() -> None:
    redis = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["redis"]
    maxmemory = _compose_memory_default(redis["environment"]["REDIS_MAXMEMORY"])
    memory_limit = _compose_memory_default(redis["mem_limit"])

    # AOF rewrite and allocator/client buffers are outside Redis maxmemory.
    # Keep at least 25% of the container memory available so Redis rejects
    # writes according to its policy before the kernel OOM-kills it.
    assert maxmemory <= memory_limit * 0.75


def test_start_script_generates_and_persists_the_redis_credentials() -> None:
    script = START_SCRIPT.read_text(encoding="utf-8")

    assert "REDIS_USERNAME REDIS_PASSWORD" in script
    assert "REDIS_DB REDIS_SSL_ENABLED" in script
    assert 'REDIS_PASSWORD=$(random_token)' in script
    assert 'export REDIS_USERNAME="${REDIS_USERNAME:-default}"' in script
    assert 'normalize_boolean_setting REDIS_SSL_ENABLED false' in script


def test_start_script_applies_rabbit_policy_and_migrates_the_legacy_notify_queue() -> None:
    script = START_SCRIPT.read_text(encoding="utf-8")

    assert "apply_rabbitmq_policies()" in script
    assert 'run --rm --no-deps rabbitmq-init' in script
    assert "migrate_notify_queue_contract()" in script
    assert '"x-message-ttl"' in script
    assert "messages=$messages consumers=$consumers" in script


def _compose_memory_default(expression: str) -> int:
    match = re.fullmatch(r"\$\{[^:}]+:-(\d+)([kmg])b?\}", str(expression), re.I)
    assert match, expression
    value = int(match.group(1))
    multiplier = {
        "k": 1024,
        "m": 1024 * 1024,
        "g": 1024 * 1024 * 1024,
    }[match.group(2).lower()]
    return value * multiplier
