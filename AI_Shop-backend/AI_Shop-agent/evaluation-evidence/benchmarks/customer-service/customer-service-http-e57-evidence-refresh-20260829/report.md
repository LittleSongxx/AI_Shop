# AI 客服 HTTP/LLM 全链路证据

> `EXECUTED_PENDING_HUMAN_ANSWER_REVIEW`；答案质量仍待独立人工盲审，不进入 release gate。

Run：`customer-service-http-e57-evidence-refresh-20260829`；样本：`120`；数据 SHA-256：`02a6dacc6a2aadb88c6dfb60bf7a74e2f083fcba0f9a6e82fef38c4dfa82caf3`。

标签/来源有效性：`HUMAN_APPROVED_AI_ASSISTED_ADJUDICATED`；blocking=`False`；审计 SHA-256：`de360fb527773f1f739bce4e8588ea40ba9671d1a96415010a0b8c54e0cc4987`。

| 指标 | 数值 | 分子/分母 | badcase |
|---|---:|---:|---|
| HTTP Intent Macro-F1 | 1.0 | 20.0/20 | - |
| HTTP High-risk Recall | 1.0 | 13/13 | - |
| HTTP Handoff Recall | 1.0 | 29/29 | - |
| 规则 Slot micro F1 | 0.966102 | 1368/1416 | cs-gold-v1-001, cs-gold-v1-002, cs-gold-v1-026, cs-gold-v1-029, cs-gold-v1-034, cs-gold-v1-037, cs-gold-v1-042, cs-gold-v1-044, cs-gold-v1-046, cs-gold-v1-052, cs-candidate-v2-069, cs-candidate-v2-073, cs-candidate-v2-086, cs-candidate-v2-088, cs-candidate-v2-097, cs-candidate-v2-099, cs-candidate-v2-114 |
| 规则 Slot EM | 0.823529 | 56/68 | cs-gold-v1-001, cs-gold-v1-002, cs-gold-v1-029, cs-gold-v1-034, cs-gold-v1-037, cs-gold-v1-042, cs-gold-v1-044, cs-candidate-v2-086, cs-candidate-v2-088, cs-candidate-v2-097, cs-candidate-v2-099, cs-candidate-v2-114 |

- HTTP 执行：`120/120`；转人工混淆矩阵：`{'truePositive': 29, 'falsePositive': 3, 'falseNegative': 0, 'trueNegative': 88}`。
- 引用结构无效：`0`，case：`无`；语义支持仍由人工评分。
- 本地全链路延迟 P50/P95/P99：`617.989/1289.224/8880.928 ms`，不是生产 SLO。
- Usage：input/output token `34281/1259`，Provider calls `11`，费用状态 `UNPRICED`，costCny `None`。未知费用不记为 0。
- 运行质量诊断（非人工真值）：Verifier observed/pass `86/83`；安全降级 `3`；澄清生效 `17`；硬约束违规 `0`，badcase：`无`。
- 定向安全行为契约：状态 `SATISFIED`；已执行/总数 `29/29`；违规 `0`，badcase：`无`。该诊断不等价于人工答案正确率。
- HTTP Episode 中的实体经过脱敏，故 HTTP Slot F1/EM 明确为 `UNAVAILABLE`；槽位只报告规则预路由结果。
- 原始逐 case answer、sourceRefs、Episode/step、tool、usage、状态 diff 均在 `report.json`，人工答案盲审表绑定该文件哈希。
