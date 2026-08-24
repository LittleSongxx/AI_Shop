# AI 客服 HTTP/LLM 全链路证据

> `STALE_WORKER_RUNTIME_DIAGNOSTIC`；答案质量仍待独立人工盲审，不进入 release gate。

Run：`customer-service-http-v11-targeted-20260824`；样本：`10`；数据 SHA-256：`112dfd6ba7546b7cbad317597d944e3ab4dc02627d4ca6018733031d8eddc527`。

| 指标 | 数值 | 分子/分母 | badcase |
|---|---:|---:|---|
| HTTP Intent Macro-F1 | 1.0 | 3.0/3 | - |
| HTTP High-risk Recall | None | 0/0 | - |
| HTTP Handoff Recall | None | 0/0 | - |
| 规则 Slot micro F1 | 1.0 | 230/230 | - |
| 规则 Slot EM | 1.0 | 8/8 | - |

- HTTP 执行：`10/10`；转人工混淆矩阵：`{'truePositive': 0, 'falsePositive': 0, 'falseNegative': 0, 'trueNegative': 10}`。
- 引用结构无效：`0`，case：`无`；语义支持仍由人工评分。
- 本地全链路延迟 P50/P95/P99：`1056.131/11344.377/11827.695 ms`，不是生产 SLO。
- Usage：input/output token `22475/1290`，Provider calls `5`，费用状态 `UNPRICED`，costCny `None`。未知费用不记为 0。
- 运行质量诊断（非人工真值）：Verifier observed/pass `10/10`；安全降级 `0`；澄清生效 `0`；硬约束违规 `0`，badcase：`无`。
- 定向安全行为契约：状态 `VIOLATIONS_DETECTED`；已执行/总数 `10/10`；违规 `6`，badcase：`cs-gold-v1-017, cs-gold-v1-019, cs-gold-v1-039, cs-gold-v1-054, cs-gold-v1-055, cs-gold-v1-056`。该诊断不等价于人工答案正确率。
- HTTP Episode 中的实体经过脱敏，故 HTTP Slot F1/EM 明确为 `UNAVAILABLE`；槽位只报告规则预路由结果。
- 原始逐 case answer、sourceRefs、Episode/step、tool、usage、状态 diff 均在 `report.json`，人工答案盲审表绑定该文件哈希。
