# 缺口取舍说明（2026-08-03）

把"智能客服垂类设计哲学"调研中发现的缺口过了一遍，按**确定性 × 面试价值 × 成本**三围
筛出两批：已补的见下文"已实现"，**明确不做**的在这里说明理由，避免后人重复评估。

## 已实现（对应代码位置）

| 缺口 | 实现 |
|---|---|
| 会话级意图延续（意图不每轮重猜） | `resolve_intent(..., session_intent)` + `looks_like_intent_continuation`，数据来自 `get_recent_intents` |
| 死循环转人工（连续同意图） | `_apply_handoff_policy` 的 `REPEATED_INTENT` 分支（≥3 轮同意图且仍需工具 → HANDOFF_SUGGESTED） |
| 结果层评测（Verified-Action 雏形） | 评测新增 `verifiedAction` 维度 + `_refund_tool_call` 拒绝"不可退也发起提案" |
| LLM 真实 token 用量 | `agent_llm_tokens_total{kind}` / `agent_llm_call_total{model,fallback}`（provider usage 字段） |
| 转人工原因可查询 | `agent_handoff_total{reason}` |
| 悬挂动作补终态 | `pending_action_service.reconcile_stale_executing`（Worker 周期扫描 EXECUTING 超时动作） |
| 语义缓存误报盲评 | `RAG_CACHE_SAMPLE_RATE` 命中抽样进 Redis 盲评队列 |
| prompt 前缀缓存契约 | system prompt 静态段前置 + 稳定性测试（`test_system_prompt_static_prefix_stable_across_calls`） |

## 明确不做（设计取舍，非遗忘）

1. **全量知识版本回滚**：当前"版本号即提交点"是增量写入 + 版本切换，重新发布的文档会在 ES
   里被同 chunk id 覆盖盖章。回滚到旧版本后，期间被重新发布的文档不可见。真回滚需要
   影子索引（每次发布建独立索引 + 别名切换 + 旧代际保留 7 天），这是重架构，与
   `spring-ai` 的单一索引绑定冲突，留作生产化阶段。单文档级"撤回"已由 `archive` 覆盖。
2. **知识版本灰度发布**（10%→100% 按用户路由）：现有 A/B 分桶只覆盖检索参数，
   版本灰度需要 Java 发布管线支持双版本并存，依赖影子索引（同上）。
3. **LLM-as-judge 生成质量评测**：本项目评测全部确定性（可冻结、可复现），这是特点不是
   缺陷；生成质量维度的覆盖留给另一个项目（RAGAS 脚本已就绪，需真实模型跑）。
4. **线上采样评测**（3-5% 流量回放）：需要真实线上流量与采样管道，属于部署后运营，
   开发环境无法验证。
5. **跨会话恢复**（30 天级会话续接）：agent_message 已全量持久化，但无显式恢复入口，
   属产品需求而非正确性缺陷。
6. **多智能体 / 端侧 / 推理优化 / LoRA 微调**：按用户规划放在另一个项目体现；
   审计报告 P2-1 的判断仍然成立——单 Agent 的质量瓶颈先由评测证明，再谈多 Agent。
