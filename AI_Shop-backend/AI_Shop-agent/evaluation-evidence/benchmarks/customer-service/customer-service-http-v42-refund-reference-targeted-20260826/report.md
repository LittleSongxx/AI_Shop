# AI 客服 HTTP/LLM 全链路证据

> `TARGETED_REFUND_REFERENCE_FIX_VALIDATED`；答案质量仍待独立人工盲审，不进入 release gate。

Run：`customer-service-http-v42-refund-reference-targeted-20260826`；样本：`2`；数据 SHA-256：`ab5129a73cf6f986173d92e3f5f04ab7e8689bae9ad4c7d7294fa13b587ee079`。

标签/来源有效性：`BLOCKED_HUMAN_READJUDICATION`；blocking=`True`；审计 SHA-256：`cbe8edceeeb7133250fcc9bac574773f8a4d347af15faa5d019d23ed65148492`。

| 指标 | 数值 | 分子/分母 | badcase |
|---|---:|---:|---|
| HTTP Intent Macro-F1 | 1.0 | 1.0/1 | - |
| HTTP High-risk Recall | None | 0/0 | - |
| HTTP Handoff Recall | None | 0/0 | - |
| 规则 Slot micro F1 | 1.0 | 20/20 | - |
| 规则 Slot EM | 1.0 | 2/2 | - |

- HTTP 执行：`2/2`；转人工混淆矩阵：`{'truePositive': 0, 'falsePositive': 0, 'falseNegative': 0, 'trueNegative': 2}`。
- 引用结构无效：`0`，case：`无`；语义支持仍由人工评分。
- 本地全链路延迟 P50/P95/P99：`451.128/499.685/504.001 ms`，不是生产 SLO。
- Usage：input/output token `0/0`，Provider calls `0`，费用状态 `NOT_APPLICABLE`，costCny `None`。未知费用不记为 0。
- 运行质量诊断（非人工真值）：Verifier observed/pass `2/2`；安全降级 `0`；澄清生效 `2`；硬约束违规 `0`，badcase：`无`。
- 定向安全行为契约：状态 `PARTIAL_NOT_EXECUTED`；已执行/总数 `2/22`；违规 `0`，badcase：`无`。该诊断不等价于人工答案正确率。
- HTTP Episode 中的实体经过脱敏，故 HTTP Slot F1/EM 明确为 `UNAVAILABLE`；槽位只报告规则预路由结果。
- 原始逐 case answer、sourceRefs、Episode/step、tool、usage、状态 diff 均在 `report.json`，人工答案盲审表绑定该文件哈希。
