# Simlect 上线清单（Go-Live）

## 结论口径

完成下列 **必做项** 并冒烟通过后，可视为 **READY WITH CONDITIONS**（无 Seata，跨库靠 Feign + Outbox/补偿）。

## 0. 构建

```bash
export JAVA_HOME=...   # JDK 17
cd Simlect/Simlect-backend
mvn -q package -DskipTests
# 确认各模块 target/*-1.0.0.jar 存在
```

## 1. 数据库（按序）

```text
00_create_databases.sql
00b_nacos_seata_databases.sql   # 首次：nacos / seata 库
14_nacos.sql / 15_seata.sql / 16_seata_undo_log.sql
01_user.sql … 10_admin.sql
13_mq_infra_per_service.sql   # user/product/search Outbox + mq_compensation_log
```

表归属见 `sql/TABLE_OWNERSHIP.md`。

## 2. 基础设施

- MySQL 8（各 simlect_* 库）
- Redis
- RabbitMQ（建议磁盘持久化）
- Nacos 2.x（8848）
- Elasticsearch（search/RAG；可先不上线搜索）
- Sentinel Dashboard（可选）

## 3. 环境变量

复制 `deploy/env.production.example`，至少改掉：

- `SIMLECT_PRODUCTION_READY=true`
- `SIMLECT_INTERNAL_TOKEN`
- `MYSQL_PASSWORD` / `RABBIT_PASSWORD`
- `ADMIN_PASSWORD`
- `PROJECT_FOLDER` / `PROJECT_DOMAIN`
- 支付宝证书与 `ALIPAY_*`（若开放支付）

## 4. 启动顺序

1. gateway:8080
2. user / product / stock / cart / coupon / order / pay / search / admin
3. Python agent:7050（`JAVA_WEB_URL` 指向 Gateway）
4. Nginx 反代（见 `deploy/nginx.simlect.conf.example`）
5. 前端 production 构建，相对路径 `/api`、`/admin-api`

## 5. 冒烟

- Nacos 服务全部 UP  
- C 端注册/登录  
- 商品列表与详情  
- 加购 → 下单 → 支付回调（或沙箱）  
- 订单超时关单 / Outbox 有 SENT 记录  
- 管理端登录与订单列表  
- 管理端首页今日数据 / 库存预警（依赖 order、user、product、stock 均 UP）  
- Agent HTTP/WS 经 Gateway

## 6. 安全确认

- 业务端口（8084–8111、7050）不对公网  
- 未使用 `-Ddev` / `SIMLECT_DEV_LOGIN_BYPASS=true`  
- Nginx 未反代 `/actuator`  
- 各服务启动日志出现「生产就绪校验通过」

