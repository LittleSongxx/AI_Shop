# AI_Shop 本地中间件（Docker）一键启动

同一网络：`aishop-net`（MySQL / Redis / RabbitMQ / Nacos / ES / Sentinel / **Seata**）。

## 首次初始化库（必做）

MySQL healthy 后，在 PowerShell：

```powershell
# 在仓库根目录 AI_Shop/ 下执行
$mysql = { param($f) Get-Content $f -Raw -Encoding UTF8 | docker exec -i aishop-mysql mysql -uroot -proot --default-character-set=utf8mb4 }

& $mysql .\sql\00_create_databases.sql
& $mysql .\sql\00b_nacos_seata_databases.sql
& $mysql .\sql\14_nacos.sql
& $mysql .\sql\15_seata.sql
& $mysql .\sql\16_seata_undo_log.sql
# 再按 GO_LIVE 执行 01…10、13
```

或运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\init-mysql-meta.ps1
```

## 一键启动

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\start-middleware.ps1
```

```powershell
cd .\deploy
docker compose -f docker-compose.middleware.yml up -d
docker compose -f docker-compose.middleware.yml ps
```

## 端口与账号

| 服务 | 地址 | 账号 / 说明 |
|------|------|-------------|
| MySQL | `127.0.0.1:3306` | root / root；库含 `nacos`、`seata` |
| Redis | `127.0.0.1:6380` | 容器内仍是 6379 |
| RabbitMQ | `5673` / http://localhost:15673 | aishop / aishop；容器内 5672 / 15672 |
| Nacos | http://localhost:8848/nacos | MySQL 持久化，鉴权关闭 |
| Elasticsearch | http://127.0.0.1:9200 | 自建镜像含 **analysis-ik**（`aishop-elasticsearch:9.2.1-ik`） |
| Sentinel | http://127.0.0.1:8858 | sentinel / sentinel |
| **Seata** | TC `8092` / 控制台 `7092` | 控制台 seata / seata；注册到 Nacos `SEATA_GROUP`；镜像自建（驱动在 `/seata-server/libs`） |

> Redis / RabbitMQ / Seata 的宿主机端口刻意 **+1** 偏移，避免与本机已装的同类服务抢端口；
> 容器内端口不变，所以容器互通用默认端口即可。宿主机上的 Java 服务必须显式传
> `REDIS_PORT=6380`、`RABBIT_PORT=5673`、`SEATA_SERVER_ADDR=127.0.0.1:8092`
> （根目录 `start.sh` 已内置），否则会落到 `application.yml` 里 6379/5672/8091 的默认值而连不上。

容器互通主机名：`mysql`、`redis`、`rabbitmq`、`nacos`、`elasticsearch`、`sentinel`、`seata-server`。  
宿主机上的 Java 微服务仍连 `127.0.0.1`（Seata TC 已映射 `8092`）。

## Seata 事务组

- 模式：**AT**（`seata.data-source-proxy-mode: AT` + 自动数据源代理）
- 客户端仅 order/stock/coupon/cart/pay 引入 Seata；admin 等不引入
- 客户端 `tx-service-group`: `aishop_tx_group` → `127.0.0.1:8092`（file registry + grouplist）
- 下单入口：`OrderInfoServiceImpl.postOrder` 使用 `@GlobalTransactional`
- 业务库 `undo_log`：`aishop_order` / `aishop_stock` / `aishop_coupon` / `aishop_cart` / `aishop_pay`（`sql/16_seata_undo_log.sql`）

## 业务侧环境变量（补充）

```text
SEATA_TX_GROUP=aishop_tx_group
NACOS_ADDR=127.0.0.1:8848
```
