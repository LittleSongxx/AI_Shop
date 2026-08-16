from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy/docker-compose.middleware.yml"
APPLICATION = ROOT / "deploy/seata/application.yml"
START_SCRIPT = ROOT / "start.sh"
POWERSHELL_SCRIPT = ROOT / "deploy/start-middleware.ps1"


def test_local_seata_requires_a_reachable_non_127_registration_address() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    script = START_SCRIPT.read_text(encoding="utf-8")
    powershell = POWERSHELL_SCRIPT.read_text(encoding="utf-8")

    assert "${SEATA_IP:?set SEATA_IP via start.sh or start-middleware.ps1}" in compose
    assert "detect_seata_ip()" in script
    assert "ip -4 -o addr show dev lo scope global" in script
    assert '"$SEATA_IP" != 127.*' in script
    assert "SEATA_IP" in script.split("RUNTIME_SETTING_VARIABLES=(", 1)[1].split(")", 1)[0]
    assert "$env:SEATA_IP" in powershell


def test_local_seata_uses_a_bounded_pool_and_non_root_store_identity() -> None:
    config = APPLICATION.read_text(encoding="utf-8")
    compose = COMPOSE.read_text(encoding="utf-8")

    assert "user: ${SEATA_MYSQL_USER}" in config
    assert "password: ${SEATA_MYSQL_PASSWORD}" in config
    assert "min-conn: ${SEATA_DB_MIN_CONN:1}" in config
    assert "max-conn: ${SEATA_DB_MAX_CONN:8}" in config
    assert "MYSQL_ROOT_PASSWORD:" not in compose.split("seata-server:", 1)[1]
