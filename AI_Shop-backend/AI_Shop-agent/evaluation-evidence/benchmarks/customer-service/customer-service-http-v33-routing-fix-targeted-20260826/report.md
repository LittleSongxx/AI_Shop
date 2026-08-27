# AI 客服 HTTP/LLM 全链路证据

> `TARGETED_POST_FIX_SMOKE_NON_GATING`；答案质量仍待独立人工盲审，不进入 release gate。

Run：`customer-service-http-v33-routing-fix-targeted-20260826`；样本：`14`；数据 SHA-256：`ab5129a73cf6f986173d92e3f5f04ab7e8689bae9ad4c7d7294fa13b587ee079`。

| 指标 | 数值 | 分子/分母 | badcase |
|---|---:|---:|---|
| HTTP Intent Macro-F1 | 0.714286 | 5.0/7 | cs-candidate-v2-112 |
| HTTP High-risk Recall | 1.0 | 3/3 | - |
| HTTP Handoff Recall | 1.0 | 6/6 | - |
| 规则 Slot micro F1 | 1.0 | 228/228 | - |
| 规则 Slot EM | 1.0 | 10/10 | - |

- HTTP 执行：`14/14`；转人工混淆矩阵：`{'truePositive': 6, 'falsePositive': 0, 'falseNegative': 0, 'trueNegative': 8}`。
- 引用结构无效：`0`，case：`无`；语义支持仍由人工评分。
- 本地全链路延迟 P50/P95/P99：`733.542/1445.497/1746.327 ms`，不是生产 SLO。
- Usage：input/output token `0/0`，Provider calls `0`，费用状态 `NOT_APPLICABLE`，costCny `None`。未知费用不记为 0。
- 运行质量诊断（非人工真值）：Verifier observed/pass `8/8`；安全降级 `0`；澄清生效 `0`；硬约束违规 `0`，badcase：`无`。
- 定向安全行为契约：状态 `VIOLATIONS_DETECTED`；已执行/总数 `7/20`；违规 `3`，badcase：`cs-candidate-v2-061, cs-candidate-v2-062, cs-candidate-v2-067`。该诊断不等价于人工答案正确率。
- HTTP Episode 中的实体经过脱敏，故 HTTP Slot F1/EM 明确为 `UNAVAILABLE`；槽位只报告规则预路由结果。
- 原始逐 case answer、sourceRefs、Episode/step、tool、usage、状态 diff 均在 `report.json`，人工答案盲审表绑定该文件哈希。
