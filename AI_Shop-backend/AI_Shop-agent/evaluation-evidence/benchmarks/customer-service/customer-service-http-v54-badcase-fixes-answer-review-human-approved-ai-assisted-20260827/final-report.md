# 客服 HTTP 答案人工质量证据

> `HUMAN_REVIEWED_ADJUDICATED`；样本 `120`；不是线上 CSAT/FCR。

| 指标 | 数值 | 分子/分母 | 95% CI | badcase |
|---|---:|---:|---|---|
| 答案正确率 | 0.966667 | 116/120 | [0.91742, 0.986962] | cs-gold-v1-036, cs-candidate-v2-075, cs-candidate-v2-110, cs-candidate-v2-116 |
| 引用语义支持率 | 0.940299 | 63/67 | [0.856305, 0.976541] | cs-gold-v1-036, cs-gold-v1-048, cs-candidate-v2-090, cs-candidate-v2-092 |
| 转人工适当率 | 1.0 | 120/120 | [0.968981, 1.0] | - |
| Unsafe-answer rate（越低越好） | 0.008333 | 1/120 | [0.001473, 0.045696] | cs-candidate-v2-110 |
| 联合质量通过率 | 0.941667 | 113/120 | [0.884474, 0.971459] | cs-gold-v1-036, cs-gold-v1-048, cs-candidate-v2-075, cs-candidate-v2-090, cs-candidate-v2-092, cs-candidate-v2-110, cs-candidate-v2-116 |

双人案件级一致：`112/120`；仲裁：`8`。

逐项标签、评论和 badcase 位于同包 `final-report.json`/`badcases.jsonl`。
