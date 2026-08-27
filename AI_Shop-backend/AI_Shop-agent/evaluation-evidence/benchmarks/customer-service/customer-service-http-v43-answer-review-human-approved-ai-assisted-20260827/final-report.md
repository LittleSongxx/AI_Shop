# 客服 HTTP 答案人工质量证据

> `HUMAN_REVIEWED_ADJUDICATED`；样本 `120`；不是线上 CSAT/FCR。

| 指标 | 数值 | 分子/分母 | 95% CI | badcase |
|---|---:|---:|---|---|
| 答案正确率 | 0.891667 | 107/120 | [0.823446, 0.935589] | cs-gold-v1-012, cs-candidate-v2-067, cs-candidate-v2-070, cs-candidate-v2-078, cs-candidate-v2-079, cs-candidate-v2-090, cs-candidate-v2-091, cs-candidate-v2-092, cs-candidate-v2-099, cs-candidate-v2-101, cs-candidate-v2-104, cs-candidate-v2-111, cs-candidate-v2-114 |
| 引用语义支持率 | 0.942857 | 66/70 | [0.86208, 0.977556] | cs-gold-v1-012, cs-candidate-v2-067, cs-candidate-v2-103, cs-candidate-v2-104 |
| 转人工适当率 | 0.983333 | 118/120 | [0.941264, 0.995417] | cs-candidate-v2-061, cs-candidate-v2-111 |
| Unsafe-answer rate（越低越好） | 0.008333 | 1/120 | [0.001473, 0.045696] | cs-candidate-v2-111 |
| 联合质量通过率 | 0.875 | 105/120 | [0.803971, 0.922765] | cs-gold-v1-012, cs-candidate-v2-061, cs-candidate-v2-067, cs-candidate-v2-070, cs-candidate-v2-078, cs-candidate-v2-079, cs-candidate-v2-090, cs-candidate-v2-091, cs-candidate-v2-092, cs-candidate-v2-099, cs-candidate-v2-101, cs-candidate-v2-103, cs-candidate-v2-104, cs-candidate-v2-111, cs-candidate-v2-114 |

双人案件级一致：`117/120`；仲裁：`3`。

逐项标签、评论和 badcase 位于同包 `final-report.json`/`badcases.jsonl`。
