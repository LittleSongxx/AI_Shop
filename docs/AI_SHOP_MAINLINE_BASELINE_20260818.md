# AI_Shop 主线改造基线

> 基线日期：2026-08-18（Asia/Shanghai）
>
> 分支：`refactor/ai-shop-mainline-quality`
>
> 起点：`e11f1e096dc3d3afef0a56446bb1b9d27d3ef9f6`

## 目标

本分支只围绕两条业务主线继续演进：

1. Java 电商底座 → 文本/视觉 AI 推荐导购 → 权威价格/库存 → 推荐事件归因。
2. AI 客服 → 发布版政策 RAG → Java 订单权威事实 → 用户确认 → 幂等执行 / `INCONCLUSIVE` / `MANUAL_REVIEW`。

视觉搜索属于推荐主线；Text2SQL 暂作为受治理的后台分析能力，只有满足独立质量门槛后才升级为第三条主线。

## 当前可复现基线

Python 统一使用 Conda `shop` 环境：

```bash
conda run -n shop python -m pytest -q
conda run -n shop ruff check app benchmarks tests
conda run -n shop python scripts/check_evidence_manifest.py
conda run -n shop python scripts/check_docs_consistency.py
```

确定性评测只证明规则和生产决策内核，不代表真实模型质量：

| 套件 | 当前结果 | 证据边界 |
|---|---:|---|
| deterministic-commerce | 27/27 | E1 deterministic；无模型调用、无 token 成本 |
| deterministic-safety | 18/18 | 合成攻击集；不代表开放世界安全 |
| deterministic-search-rag | 162/162 | 数据/query 契约；未采集 live Recall/MRR |

历史正式质量结果仍按失败保留：Search ProductService 首次 `Recall@10=0.3778`；RAG v4 fresh `Recall@5=0.8056`、生成 `39/60`；暴露后的 replay 不得升级为 fresh 证据。Agent v2 当前为 `resultStatus=NOT_COLLECTED`。

## 已知阻塞

- 正式 live 评测需要真实 LLM、Embedding、Rerank、VLM、Java Gateway、MCP、Worker、数据库和隔离 fixture 同时可用。
- 当前交接记录中的 Rerank 配置曾返回 `401 invalid_api_key`，VLM 曾返回 `403 insufficient_quota`；正式 Runner 必须 fail-closed，不能用 fallback 掩盖 Provider 缺失。
- 评测状态机已要求成功终态为 `PACKAGED/COMPLETE`；质量失败保留为 `FAILED_RETAINED`，依赖/Provider 阻塞为 `BLOCKED`。
- 不采集真实用户 Pilot；未取得授权样本前不得声称 `REAL_USER`、CTR、CVR、GMV 或生产 SLO。

## 改造顺序

1. 评测生命周期、CI 终态和证据 manifest。
2. 推荐/客服版本化契约与领域边界。
3. 真实 Provider 受控对比、Search/RAG/Visual/Agent 门禁。
4. API/Worker/MQ/MCP/Java 的 Trace、Episode、Outcome 回流。
5. Text2SQL 40 条锁定任务和第三主线准入判定。
6. README、项目卡、架构图、badcase 复盘和面试故事。
