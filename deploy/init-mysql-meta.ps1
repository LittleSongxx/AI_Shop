# 初始化 Nacos / Seata / undo_log（以及可选业务库）
# 要求：aishop-mysql 已 healthy

$ErrorActionPreference = "Stop"
$SqlRoot = Join-Path (Split-Path $PSScriptRoot -Parent) "sql"

function Invoke-MysqlFile([string]$path) {
  if (-not (Test-Path $path)) { throw "missing $path" }
  Write-Host "==> $path"
  Get-Content $path -Raw -Encoding UTF8 | docker exec -i aishop-mysql mysql -uroot -proot --default-character-set=utf8mb4
}

$health = docker inspect -f "{{.State.Health.Status}}" aishop-mysql 2>$null
if ($health -ne "healthy") {
  throw "aishop-mysql 未就绪（status=$health）。请先 docker compose up -d mysql"
}

Invoke-MysqlFile (Join-Path $SqlRoot "00_create_databases.sql")
Invoke-MysqlFile (Join-Path $SqlRoot "00b_nacos_seata_databases.sql")
Invoke-MysqlFile (Join-Path $SqlRoot "14_nacos.sql")
Invoke-MysqlFile (Join-Path $SqlRoot "15_seata.sql")
Invoke-MysqlFile (Join-Path $SqlRoot "16_seata_undo_log.sql")

Write-Host "OK: nacos / seata / undo_log 已初始化"
Write-Host "业务表由 Java Flyway 与 Python Alembic 在服务启动时自动初始化/升级"
