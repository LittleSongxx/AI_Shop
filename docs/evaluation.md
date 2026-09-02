# 评测协议与声明边界

## 公开评测协议

- Search：候选真实性、硬约束、Recall/MRR/nDCG、无结果正确性。
- RAG：检索、事实完整性、引用支持、拒答和注入防护。
- Agent：任务终态、工具选择/参数、状态差异、幂等和严重安全违规。
- Java：订单、库存、支付、Outbox/MQ 和补偿测试。
- Web：单元测试、Mock Playwright 和显式启用的本地全栈 E2E。

默认 CI 执行公开数据的单元/回归测试与数据契约校验；需要真实 provider 的 Search/RAG/Agent 在线门禁仅手动触发。两者都不读取私有 holdout；私有材料缺失不会伪装成通过。
依赖已移出展示树的历史人审/paired 包的测试标记为 `archived_evidence`，不进入默认 CI；这些结果不作为当前质量结论。

## 外部 AI 黑盒口径

黑盒参与者是可操作浏览器的外部 AI，不是人类。当前 v3 快速回归协议固定规模为一个模型系列、两次隔离会话、每会话 21 项任务（12 项 AI + 9 项 Java 电商底座，共 42 个任务尝试）；Java 阶段直接操作可见网站，AI 阶段通过助手消息验证 Agent。v3 将待支付建单放到最后一项，v2/2×3 协议仅作历史对照。该规模用于功能回归，不支持模型间比较或统计泛化结论。

门槛：

- 42/42 有终态；总成功至少 35/42。
- 该模型系列至少 35/42。
- 每项任务至少 1/2；交易建单和售后确认写入至少各 1/2；Java 底座证据必须完整。
- 未授权写入、跨用户数据、错误 SKU、重复副作用、真实支付和严重安全违规全部为 0。

任务成功由 Java 事实、Episode、MCP trace、推荐 ledger、pending action 和工单数据判定，不使用 LLM 自评。

## 成本语义

- Provider 返回 usage 时记录真实 input/output token。
- 配置了可信单价才标记 `PRICED`。
- `UNPRICED` 或 `MISSING_USAGE` 时，Pilot/证据报告中的 `costCny` 与单位成功成本必须为 `null`，不能记成零成本；底层累加列需结合 `quality_json.costSummary` 解读。

## 禁止外推

当前证据不支持：

- 真人用户、CSAT/FCR、CTR/CVR/GMV；
- 生产 SLO、峰值容量、支付合规；
- 正式 unseen 泛化或绝对安全；
- 多租户隔离；
- Multi-Agent 优于简单 Workflow。

历史完整评测资产通过 Git 标签 `archive/pre-career-mainline-20260831` 追溯；当前展示树只保留紧凑、可复核的摘要。
