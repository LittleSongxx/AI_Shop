# AI 客服 HTTP/LLM 全链路证据

> `TARGETED_FINAL_SEVEN_REGRESSIONS_EXECUTED_PENDING_HUMAN_REVIEW`；答案质量仍待独立人工盲审，不进入 release gate。

Run：`customer-service-http-v55-final-seven-targeted-r2-20260827`；样本：`7`；数据 SHA-256：`02a6dacc6a2aadb88c6dfb60bf7a74e2f083fcba0f9a6e82fef38c4dfa82caf3`。

标签/来源有效性：`HUMAN_APPROVED_AI_ASSISTED_ADJUDICATED`；blocking=`False`；审计 SHA-256：`de360fb527773f1f739bce4e8588ea40ba9671d1a96415010a0b8c54e0cc4987`。

| 指标 | 数值 | 分子/分母 | badcase |
|---|---:|---:|---|
| HTTP Intent Macro-F1 | 1.0 | 5.0/5 | - |
| HTTP High-risk Recall | None | 0/0 | - |
| HTTP Handoff Recall | None | 0/0 | - |
| 规则 Slot micro F1 | 1.0 | 46/46 | - |
| 规则 Slot EM | 1.0 | 2/2 | - |

- HTTP 执行：`7/7`；转人工混淆矩阵：`{'truePositive': 0, 'falsePositive': 0, 'falseNegative': 0, 'trueNegative': 7}`。
- 引用结构无效：`0`，case：`无`；语义支持仍由人工评分。
- 本地全链路延迟 P50/P95/P99：`390.56/988.777/1182.202 ms`，不是生产 SLO。
- Usage：input/output token `0/0`，Provider calls `0`，费用状态 `NOT_APPLICABLE`，costCny `None`。未知费用不记为 0。
- 运行质量诊断（非人工真值）：Verifier observed/pass `7/7`；安全降级 `0`；澄清生效 `1`；硬约束违规 `0`，badcase：`无`。
- 定向安全行为契约：状态 `PARTIAL_NOT_EXECUTED`；已执行/总数 `7/29`；违规 `0`，badcase：`无`。该诊断不等价于人工答案正确率。
- HTTP Episode 中的实体经过脱敏，故 HTTP Slot F1/EM 明确为 `UNAVAILABLE`；槽位只报告规则预路由结果。
- 原始逐 case answer、sourceRefs、Episode/step、tool、usage、状态 diff 均在 `report.json`，人工答案盲审表绑定该文件哈希。
