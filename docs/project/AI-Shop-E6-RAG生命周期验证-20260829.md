# E6 RAG 生命周期验证（2026-08-29）

状态：`CONTROLLED_PREPROD_ONLY`；本批证明发布、权限和 freshness 门禁按代码路径工作，
不外推为线上召回率、生产 SLO 或多租户隔离证明。

## 本批收口

- Java 知识文档和不可变发布快照增加规范化 `accessPolicy`。支持 `PUBLIC`、
  `AUTHENTICATED`、`ADMIN`、`ROLE:USER`、`ROLE:ADMIN` 和有界 `USER:<subject>`；空值兼容为 `PUBLIC`，
  通配符和未知策略拒绝。目录 SHA 将策略纳入成员 canonical，避免权限变化复用旧快照。
- Agent 每次 RAG 请求携带服务端 principal。ES 先做粗粒度 ACL filter，缓存、FAQ 和向量/
  BM25 候选在本地再执行状态、ACL、freshness 的 fail-closed backstop；缺策略的历史向量
  按单店兼容规则视为 public，显式坏策略不放行。过滤原因进入
  `agent_rag_lifecycle_filter_total{reason}` 和运行 trace。
- 发布目录只允许当前 `PUBLISHED`、活动文档和不晚于 release 的知识切片；FAQ 继续使用
  既有 `PUBLISHED` + 生效时间 SQL，单店产品策略下保持 `PUBLIC`。ISO、epoch 秒/毫秒均可
  解析，缓存命中不会因时间格式差异抛错。
- 管理端增加逻辑删除。删除已发布文档时先生成新的空缺目录快照，保留历史 chunk/vector
  供明确 rollback；重复删除幂等，归档/删除文档不能直接再次发布。RabbitMQ RAG DLQ
  增加 received/recorded/handler_error 与类型维度指标。

## 可复核检查

执行目录：`AI_Shop-backend/AI_Shop-agent`（Python）及仓库根目录（Java）。

| 范围 | 命令 | 结果 |
| --- | --- | --- |
| 生命周期/检索定向回归 | `uv run --project . pytest -q tests/test_rag_lifecycle.py tests/test_rag_retriever.py tests/test_conditional_rag.py tests/test_rag_v4_runtime.py` | `66 passed` |
| Agent lint | `uv run --project . ruff check app/rag/lifecycle.py app/rag/retriever.py app/services/mcp_tool_router.py app/graph/nodes.py tests/test_rag_lifecycle.py` | `All checks passed` |
| Java search 模块回归 | `mvn -B -ntp -q -f AI_Shop-backend/pom.xml -pl AI_Shop-search -am -DskipTests=false test` | `BUILD SUCCESS`；`41` tests，0 failure/error |
| 差异格式检查 | `git diff --check` | passed |

测试覆盖了匿名/用户/管理员 ACL、通配符和坏策略拒绝、发布状态、ISO/epoch freshness、
缓存与目录权限门禁、逻辑删除幂等/历史向量保留，以及既有 Java 发布/回滚/DLQ 回归。
未运行线上数据库迁移或外部 Provider；不保存密钥。

## 边界与后续

- 目标仍是单店受控闭环，不做 B2B tenant/RLS、跨租户策略、全量 fuzz 或长期 soak；FAQ
  当前显式保持 public，若要按 FAQ 角色隔离需另开范围并补管理端策略编辑。
- ES filter 是性能优化，数据库发布目录和 Agent 本地 backstop 才是授权依据；目录、Redis
  hint 或 Java 不可用时知识分支 fail-closed，不能靠固定版本继续搜旧向量。
- `releaseGateEligible=false`、`unseen=paused` 保持不变。本批只收口工程证据，不能据此
  声称线上 CTR/CVR/GMV、CSAT/FCR、生产容量或无人值守高并发能力。
- Text2SQL 仍按 [冻结说明](AI-Shop-Text2SQL冻结说明-20260829.md) 执行，本批未修改其代码或
  证据包。
