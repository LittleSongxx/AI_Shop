# AI_Shop 本地中间件一键启动（Windows PowerShell）
$ErrorActionPreference = "Stop"
$ComposeFile = Join-Path $PSScriptRoot "docker-compose.middleware.yml"

if ([string]::IsNullOrWhiteSpace($env:MYSQL_ROOT_PASSWORD)) {
  $env:MYSQL_ROOT_PASSWORD = "root"
}
if ([string]::IsNullOrWhiteSpace($env:NACOS_MYSQL_USER)) {
  $env:NACOS_MYSQL_USER = "nacos_app"
}
if ([string]::IsNullOrWhiteSpace($env:SEATA_MYSQL_USER)) {
  $env:SEATA_MYSQL_USER = "seata_app"
}
foreach ($identity in @($env:NACOS_MYSQL_USER, $env:SEATA_MYSQL_USER)) {
  if ($identity -notmatch "^[A-Za-z0-9_]+$" -or $identity.ToLowerInvariant() -eq "root") {
    throw "Nacos/Seata MySQL 账号必须是非 root 的字母、数字或下划线账号"
  }
}
if ($env:NACOS_MYSQL_USER -eq $env:SEATA_MYSQL_USER) {
  throw "Nacos 与 Seata 必须使用不同的 MySQL 账号"
}
if ([string]::IsNullOrWhiteSpace($env:NACOS_MYSQL_PASSWORD)) {
  $env:NACOS_MYSQL_PASSWORD = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
}
if ([string]::IsNullOrWhiteSpace($env:SEATA_MYSQL_PASSWORD)) {
  $env:SEATA_MYSQL_PASSWORD = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
}

if ([string]::IsNullOrWhiteSpace($env:SEATA_IP)) {
  $network = Get-NetIPConfiguration |
    Where-Object {
      $_.IPv4DefaultGateway -ne $null -and
      $_.IPv4Address -ne $null
    } |
    Select-Object -First 1
  if ($null -eq $network) {
    throw "无法确定 Seata 可注册地址，请显式设置 SEATA_IP 为本机非 127/8 的 IPv4 地址"
  }
  $env:SEATA_IP = ($network.IPv4Address | Select-Object -First 1).IPAddress
  Write-Warning "Seata 2.5 不接受 127.0.0.1 注册；TC 将绑定 $($env:SEATA_IP):8092，请仅允许可信网络访问"
}
if ($env:SEATA_IP -eq "0.0.0.0" -or $env:SEATA_IP.StartsWith("127.")) {
  throw "SEATA_IP 必须是本机非 127/8 的 IPv4 地址"
}

Write-Host "==> 可选：提高 ES vm.max_map_count"
try {
  wsl -d docker-desktop -u root -- sysctl -w vm.max_map_count=262144 2>$null | Out-Null
} catch { }

Write-Host "==> 确保 MySQL 先起来（便于初始化 nacos/seata 库）"
docker compose -f $ComposeFile up -d mysql
$ok = $false
for ($i = 0; $i -lt 60; $i++) {
  $st = docker inspect -f "{{.State.Health.Status}}" aishop-mysql 2>$null
  if ($st -eq "healthy") { $ok = $true; break }
  Start-Sleep -Seconds 2
}
if (-not $ok) { throw "MySQL 未在超时内 healthy" }

Write-Host "==> 初始化 nacos / seata / undo_log（可重复执行）"
& (Join-Path $PSScriptRoot "init-mysql-meta.ps1")

Write-Host "==> 配置 Nacos / Seata 独立 MySQL 账号"
$nacosPassword = $env:NACOS_MYSQL_PASSWORD.Replace("'", "''")
$seataPassword = $env:SEATA_MYSQL_PASSWORD.Replace("'", "''")
$identitySql = @"
SET SESSION sql_mode='NO_BACKSLASH_ESCAPES';
CREATE USER IF NOT EXISTS '$($env:NACOS_MYSQL_USER)'@'%' IDENTIFIED BY '$nacosPassword';
ALTER USER '$($env:NACOS_MYSQL_USER)'@'%' IDENTIFIED BY '$nacosPassword';
REVOKE ALL PRIVILEGES, GRANT OPTION FROM '$($env:NACOS_MYSQL_USER)'@'%';
GRANT ALL PRIVILEGES ON nacos.* TO '$($env:NACOS_MYSQL_USER)'@'%';
CREATE USER IF NOT EXISTS '$($env:SEATA_MYSQL_USER)'@'%' IDENTIFIED BY '$seataPassword';
ALTER USER '$($env:SEATA_MYSQL_USER)'@'%' IDENTIFIED BY '$seataPassword';
REVOKE ALL PRIVILEGES, GRANT OPTION FROM '$($env:SEATA_MYSQL_USER)'@'%';
GRANT ALL PRIVILEGES ON seata.* TO '$($env:SEATA_MYSQL_USER)'@'%';
"@
docker exec `
  -e "MYSQL_PWD=$($env:MYSQL_ROOT_PASSWORD)" `
  aishop-mysql `
  mysql --protocol=TCP --host=127.0.0.1 --port=3306 --user=root --execute=$identitySql
if ($LASTEXITCODE -ne 0) {
  throw "Nacos / Seata MySQL 账号配置失败"
}

Write-Host "==> 启动全部中间件（同网络 aishop-net，含 Seata）"
docker compose -f $ComposeFile up -d

Write-Host ""
docker compose -f $ComposeFile ps
Write-Host ""
Write-Host "MySQL 3306 | Redis 6380 | Rabbit 5673/15673 | Nacos 8848 | ES 9200 | Sentinel 8858 | Seata TC $($env:SEATA_IP):8092"
