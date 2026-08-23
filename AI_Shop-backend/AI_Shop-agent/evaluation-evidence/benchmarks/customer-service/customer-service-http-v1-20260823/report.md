# AI 客服 HTTP/LLM 全链路证据

> `EXECUTED_PENDING_HUMAN_ANSWER_REVIEW`；答案质量仍待独立人工盲审，不进入 release gate。

Run：`customer-service-http-v1-20260823`；样本：`60`；数据 SHA-256：`112dfd6ba7546b7cbad317597d944e3ab4dc02627d4ca6018733031d8eddc527`。

| 指标 | 数值 | 分子/分母 | badcase |
|---|---:|---:|---|
| HTTP Intent Macro-F1 | 0.955299 | 19.105978/20 | cs-gold-v1-011, cs-gold-v1-044, cs-gold-v1-057 |
| HTTP High-risk Recall | 1.0 | 10/10 | - |
| HTTP Handoff Recall | 1.0 | 14/14 | - |
| 规则 Slot micro F1 | 0.907652 | 688/758 | cs-gold-v1-001, cs-gold-v1-002, cs-gold-v1-003, cs-gold-v1-009, cs-gold-v1-020, cs-gold-v1-029, cs-gold-v1-033, cs-gold-v1-034, cs-gold-v1-041, cs-gold-v1-042, cs-gold-v1-043, cs-gold-v1-045, cs-gold-v1-055, cs-gold-v1-058, cs-gold-v1-059 |
| 规则 Slot EM | 0.558824 | 19/34 | cs-gold-v1-001, cs-gold-v1-002, cs-gold-v1-003, cs-gold-v1-009, cs-gold-v1-020, cs-gold-v1-029, cs-gold-v1-033, cs-gold-v1-034, cs-gold-v1-041, cs-gold-v1-042, cs-gold-v1-043, cs-gold-v1-045, cs-gold-v1-055, cs-gold-v1-058, cs-gold-v1-059 |

- HTTP 执行：`60/60`；转人工混淆矩阵：`{'truePositive': 14, 'falsePositive': 0, 'falseNegative': 0, 'trueNegative': 46}`。
- 引用结构无效：`0`，case：`无`；语义支持仍由人工评分。
- 本地全链路延迟 P50/P95/P99：`1014.106/15212.514/60141.617 ms`，不是生产 SLO。
- Usage：input/output token `114720/6649`，Provider calls `32`，费用状态 `MISSING_USAGE`，costCny `None`。未知费用不记为 0。
- HTTP Episode 中的实体经过脱敏，故 HTTP Slot F1/EM 明确为 `UNAVAILABLE`；槽位只报告规则预路由结果。
- 原始逐 case answer、sourceRefs、Episode/step、tool、usage、状态 diff 均在 `report.json`，人工答案盲审表绑定该文件哈希。
