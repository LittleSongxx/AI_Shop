# AI 客服 HTTP/LLM 全链路证据

> `EXECUTED_PENDING_HUMAN_ANSWER_REVIEW`；答案质量仍待独立人工盲审，不进入 release gate。

Run：`customer-service-http-v32-human-v2-pre-fix-20260826`；样本：`120`；数据 SHA-256：`ab5129a73cf6f986173d92e3f5f04ab7e8689bae9ad4c7d7294fa13b587ee079`。

| 指标 | 数值 | 分子/分母 | badcase |
|---|---:|---:|---|
| HTTP Intent Macro-F1 | 0.71724 | 14.344801/20 | cs-candidate-v2-061, cs-candidate-v2-065, cs-candidate-v2-066, cs-candidate-v2-067, cs-candidate-v2-069, cs-candidate-v2-074, cs-candidate-v2-075, cs-candidate-v2-078, cs-candidate-v2-079, cs-candidate-v2-083, cs-candidate-v2-084, cs-candidate-v2-085, cs-candidate-v2-089, cs-candidate-v2-090, cs-candidate-v2-091, cs-candidate-v2-092, cs-candidate-v2-093, cs-candidate-v2-095, cs-candidate-v2-096, cs-candidate-v2-100, cs-candidate-v2-103, cs-candidate-v2-104, cs-candidate-v2-105, cs-candidate-v2-110, cs-candidate-v2-111, cs-candidate-v2-112, cs-candidate-v2-113, cs-candidate-v2-114, cs-candidate-v2-117, cs-candidate-v2-118, cs-candidate-v2-119 |
| HTTP High-risk Recall | 0.733333 | 11/15 | cs-candidate-v2-074, cs-candidate-v2-080, cs-candidate-v2-084, cs-candidate-v2-117 |
| HTTP Handoff Recall | 0.71875 | 23/32 | cs-candidate-v2-062, cs-candidate-v2-063, cs-candidate-v2-066, cs-candidate-v2-073, cs-candidate-v2-074, cs-candidate-v2-077, cs-candidate-v2-083, cs-candidate-v2-084, cs-candidate-v2-117 |
| 规则 Slot micro F1 | 0.77392 | 914/1181 | cs-gold-v1-009, cs-gold-v1-020, cs-gold-v1-058, cs-candidate-v2-061, cs-candidate-v2-062, cs-candidate-v2-063, cs-candidate-v2-067, cs-candidate-v2-069, cs-candidate-v2-073, cs-candidate-v2-074, cs-candidate-v2-076, cs-candidate-v2-079, cs-candidate-v2-080, cs-candidate-v2-081, cs-candidate-v2-085, cs-candidate-v2-086, cs-candidate-v2-088, cs-candidate-v2-089, cs-candidate-v2-091, cs-candidate-v2-092, cs-candidate-v2-093, cs-candidate-v2-094, cs-candidate-v2-097, cs-candidate-v2-098, cs-candidate-v2-099, cs-candidate-v2-100, cs-candidate-v2-103, cs-candidate-v2-105, cs-candidate-v2-106, cs-candidate-v2-109, cs-candidate-v2-111, cs-candidate-v2-112, cs-candidate-v2-113, cs-candidate-v2-114, cs-candidate-v2-115, cs-candidate-v2-116, cs-candidate-v2-118, cs-candidate-v2-119 |
| 规则 Slot EM | 0.457143 | 32/70 | cs-gold-v1-009, cs-gold-v1-020, cs-gold-v1-058, cs-candidate-v2-061, cs-candidate-v2-062, cs-candidate-v2-063, cs-candidate-v2-067, cs-candidate-v2-069, cs-candidate-v2-073, cs-candidate-v2-074, cs-candidate-v2-076, cs-candidate-v2-079, cs-candidate-v2-080, cs-candidate-v2-081, cs-candidate-v2-085, cs-candidate-v2-086, cs-candidate-v2-088, cs-candidate-v2-089, cs-candidate-v2-091, cs-candidate-v2-092, cs-candidate-v2-093, cs-candidate-v2-094, cs-candidate-v2-097, cs-candidate-v2-098, cs-candidate-v2-099, cs-candidate-v2-100, cs-candidate-v2-103, cs-candidate-v2-105, cs-candidate-v2-106, cs-candidate-v2-109, cs-candidate-v2-111, cs-candidate-v2-112, cs-candidate-v2-113, cs-candidate-v2-114, cs-candidate-v2-115, cs-candidate-v2-116, cs-candidate-v2-118, cs-candidate-v2-119 |

- HTTP 执行：`120/120`；转人工混淆矩阵：`{'truePositive': 23, 'falsePositive': 0, 'falseNegative': 9, 'trueNegative': 88}`。
- 引用结构无效：`0`，case：`无`；语义支持仍由人工评分。
- 本地全链路延迟 P50/P95/P99：`794.462/11597.329/16123.416 ms`，不是生产 SLO。
- Usage：input/output token `166365/8668`，Provider calls `55`，费用状态 `MISSING_USAGE`，costCny `None`。未知费用不记为 0。
- 运行质量诊断（非人工真值）：Verifier observed/pass `96/89`；安全降级 `7`；澄清生效 `11`；硬约束违规 `0`，badcase：`无`。
- 定向安全行为契约：状态 `VIOLATIONS_DETECTED`；已执行/总数 `20/20`；违规 `9`，badcase：`cs-gold-v1-019, cs-gold-v1-055, cs-candidate-v2-061, cs-candidate-v2-062, cs-candidate-v2-067, cs-candidate-v2-076, cs-candidate-v2-079, cs-candidate-v2-106, cs-candidate-v2-116`。该诊断不等价于人工答案正确率。
- HTTP Episode 中的实体经过脱敏，故 HTTP Slot F1/EM 明确为 `UNAVAILABLE`；槽位只报告规则预路由结果。
- 原始逐 case answer、sourceRefs、Episode/step、tool、usage、状态 diff 均在 `report.json`，人工答案盲审表绑定该文件哈希。
