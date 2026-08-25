# 客服 HTTP 答案人工质量证据

> `HUMAN_REVIEWED_ADJUDICATED`；样本 `60`；不是线上 CSAT/FCR。

| 指标 | 数值 | 分子/分母 | 95% CI | badcase |
|---|---:|---:|---|---|
| 答案正确率 | 0.95 | 57/60 | [0.862995, 0.98285] | cs-gold-v1-014, cs-gold-v1-018, cs-gold-v1-043 |
| 引用语义支持率 | 0.694444 | 25/36 | [0.531437, 0.819955] | cs-gold-v1-008, cs-gold-v1-009, cs-gold-v1-014, cs-gold-v1-018, cs-gold-v1-019, cs-gold-v1-020, cs-gold-v1-021, cs-gold-v1-027, cs-gold-v1-029, cs-gold-v1-043, cs-gold-v1-055 |
| 转人工适当率 | 1.0 | 60/60 | [0.939828, 1.0] | - |
| Unsafe-answer rate（越低越好） | 0.016667 | 1/60 | [0.002948, 0.088551] | cs-gold-v1-014 |
| 联合质量通过率 | 0.816667 | 49/60 | [0.700802, 0.894422] | cs-gold-v1-008, cs-gold-v1-009, cs-gold-v1-014, cs-gold-v1-018, cs-gold-v1-019, cs-gold-v1-020, cs-gold-v1-021, cs-gold-v1-027, cs-gold-v1-029, cs-gold-v1-043, cs-gold-v1-055 |

双人案件级一致：`56/60`；仲裁：`4`。

逐项标签、评论和 badcase 位于同包 `final-report.json`/`badcases.jsonl`。
