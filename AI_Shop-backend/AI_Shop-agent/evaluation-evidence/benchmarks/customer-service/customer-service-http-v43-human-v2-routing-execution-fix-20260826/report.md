# AI 客服 HTTP/LLM 全链路证据

> `EXECUTED_PENDING_HUMAN_ANSWER_REVIEW`；答案质量仍待独立人工盲审，不进入 release gate。

Run：`customer-service-http-v43-human-v2-routing-execution-fix-20260826`；样本：`120`；数据 SHA-256：`ab5129a73cf6f986173d92e3f5f04ab7e8689bae9ad4c7d7294fa13b587ee079`。

标签/来源有效性：`BLOCKED_HUMAN_READJUDICATION`；blocking=`True`；审计 SHA-256：`cbe8edceeeb7133250fcc9bac574773f8a4d347af15faa5d019d23ed65148492`。

| 指标 | 数值 | 分子/分母 | badcase |
|---|---:|---:|---|
| HTTP Intent Macro-F1 | 0.962857 | 19.257143/20 | cs-candidate-v2-112, cs-candidate-v2-113, cs-candidate-v2-114 |
| HTTP High-risk Recall | 1.0 | 15/15 | - |
| HTTP Handoff Recall | 1.0 | 32/32 | - |
| 规则 Slot micro F1 | 0.982481 | 1402/1427 | cs-gold-v1-001, cs-gold-v1-002, cs-gold-v1-026, cs-gold-v1-029, cs-gold-v1-030, cs-gold-v1-034, cs-gold-v1-037, cs-gold-v1-042, cs-gold-v1-044, cs-gold-v1-046, cs-gold-v1-052, cs-candidate-v2-097 |
| 规则 Slot EM | 0.871429 | 61/70 | cs-gold-v1-001, cs-gold-v1-002, cs-gold-v1-029, cs-gold-v1-030, cs-gold-v1-034, cs-gold-v1-037, cs-gold-v1-042, cs-gold-v1-044, cs-candidate-v2-097 |

- HTTP 执行：`120/120`；转人工混淆矩阵：`{'truePositive': 32, 'falsePositive': 0, 'falseNegative': 0, 'trueNegative': 88}`。
- 引用结构无效：`0`，case：`无`；语义支持仍由人工评分。
- 本地全链路延迟 P50/P95/P99：`623.195/6161.334/7795.156 ms`，不是生产 SLO。
- Usage：input/output token `64606/2698`，Provider calls `20`，费用状态 `UNPRICED`，costCny `None`。未知费用不记为 0。
- 运行质量诊断（非人工真值）：Verifier observed/pass `88/85`；安全降级 `3`；澄清生效 `17`；硬约束违规 `0`，badcase：`无`。
- 定向安全行为契约：状态 `SATISFIED`；已执行/总数 `22/22`；违规 `0`，badcase：`无`。该诊断不等价于人工答案正确率。
- HTTP Episode 中的实体经过脱敏，故 HTTP Slot F1/EM 明确为 `UNAVAILABLE`；槽位只报告规则预路由结果。
- 原始逐 case answer、sourceRefs、Episode/step、tool、usage、状态 diff 均在 `report.json`，人工答案盲审表绑定该文件哈希。
