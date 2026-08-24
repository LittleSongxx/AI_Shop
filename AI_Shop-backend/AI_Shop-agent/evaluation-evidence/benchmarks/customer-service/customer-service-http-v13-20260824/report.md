# AI 客服 HTTP/LLM 全链路证据

> `EXECUTED_PENDING_HUMAN_ANSWER_REVIEW`；答案质量仍待独立人工盲审，不进入 release gate。

Run：`customer-service-http-v13-20260824`；样本：`60`；数据 SHA-256：`112dfd6ba7546b7cbad317597d944e3ab4dc02627d4ca6018733031d8eddc527`。

| 指标 | 数值 | 分子/分母 | badcase |
|---|---:|---:|---|
| HTTP Intent Macro-F1 | 1.0 | 20.0/20 | - |
| HTTP High-risk Recall | 1.0 | 10/10 | - |
| HTTP Handoff Recall | 1.0 | 14/14 | - |
| 规则 Slot micro F1 | 0.996364 | 822/825 | cs-gold-v1-009, cs-gold-v1-020, cs-gold-v1-058 |
| 规则 Slot EM | 0.911765 | 31/34 | cs-gold-v1-009, cs-gold-v1-020, cs-gold-v1-058 |

- HTTP 执行：`60/60`；转人工混淆矩阵：`{'truePositive': 14, 'falsePositive': 0, 'falseNegative': 0, 'trueNegative': 46}`。
- 引用结构无效：`0`，case：`无`；语义支持仍由人工评分。
- 本地全链路延迟 P50/P95/P99：`1015.049/11372.651/22858.23 ms`，不是生产 SLO。
- Usage：input/output token `78470/5486`，Provider calls `18`，费用状态 `UNPRICED`，costCny `None`。未知费用不记为 0。
- 运行质量诊断（非人工真值）：Verifier observed/pass `46/40`；安全降级 `6`；澄清生效 `6`；硬约束违规 `0`，badcase：`无`。
- 定向安全行为契约：状态 `SATISFIED`；已执行/总数 `10/10`；违规 `0`，badcase：`无`。该诊断不等价于人工答案正确率。
- HTTP Episode 中的实体经过脱敏，故 HTTP Slot F1/EM 明确为 `UNAVAILABLE`；槽位只报告规则预路由结果。
- 原始逐 case answer、sourceRefs、Episode/step、tool、usage、状态 diff 均在 `report.json`，人工答案盲审表绑定该文件哈希。
