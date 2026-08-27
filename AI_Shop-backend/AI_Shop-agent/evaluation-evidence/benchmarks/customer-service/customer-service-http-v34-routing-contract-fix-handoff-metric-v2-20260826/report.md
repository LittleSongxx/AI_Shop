# AI 客服 HTTP/LLM 全链路证据

> `TARGETED_POST_FIX_SMOKE_NON_GATING`；答案质量仍待独立人工盲审，不进入 release gate。

Run：`customer-service-http-v34-routing-contract-fix-targeted-20260826`；样本：`3`；数据 SHA-256：`ab5129a73cf6f986173d92e3f5f04ab7e8689bae9ad4c7d7294fa13b587ee079`。

| 指标 | 数值 | 分子/分母 | badcase |
|---|---:|---:|---|
| HTTP Intent Macro-F1 | 1.0 | 2.0/2 | - |
| HTTP High-risk Recall | None | 0/0 | - |
| HTTP Handoff Recall | 1.0 | 1/1 | - |
| 规则 Slot micro F1 | 1.0 | 68/68 | - |
| 规则 Slot EM | 1.0 | 3/3 | - |

- HTTP 执行：`3/3`；转人工混淆矩阵：`{'truePositive': 1, 'falsePositive': 1, 'falseNegative': 0, 'trueNegative': 1}`。
- 引用结构无效：`0`，case：`无`；语义支持仍由人工评分。
- 本地全链路延迟 P50/P95/P99：`970.879/11593.041/12537.233 ms`，不是生产 SLO。
- Usage：input/output token `18203/629`，Provider calls `4`，费用状态 `UNPRICED`，costCny `None`。未知费用不记为 0。
- 运行质量诊断（非人工真值）：Verifier observed/pass `1/1`；安全降级 `0`；澄清生效 `0`；硬约束违规 `0`，badcase：`无`。
- 定向安全行为契约：状态 `PARTIAL_NOT_EXECUTED`；已执行/总数 `3/20`；违规 `0`，badcase：`无`。该诊断不等价于人工答案正确率。
- HTTP Episode 中的实体经过脱敏，故 HTTP Slot F1/EM 明确为 `UNAVAILABLE`；槽位只报告规则预路由结果。
- 原始逐 case answer、sourceRefs、Episode/step、tool、usage、状态 diff 均在 `report.json`，人工答案盲审表绑定该文件哈希。
