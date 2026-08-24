# 客服 HTTP 答案人工质量证据

> `HUMAN_REVIEWED_ADJUDICATED`；样本 `60`；不是线上 CSAT/FCR。

| 指标 | 数值 | 分子/分母 | 95% CI | badcase |
|---|---:|---:|---|---|
| 答案正确率 | 0.983333 | 59/60 | [0.911449, 0.997052] | cs-gold-v1-012 |
| 引用语义支持率 | 0.588235 | 20/34 | [0.422216, 0.73634] | cs-gold-v1-004, cs-gold-v1-005, cs-gold-v1-006, cs-gold-v1-007, cs-gold-v1-008, cs-gold-v1-009, cs-gold-v1-012, cs-gold-v1-014, cs-gold-v1-016, cs-gold-v1-017, cs-gold-v1-018, cs-gold-v1-019, cs-gold-v1-035, cs-gold-v1-055 |
| 转人工适当率 | 0.983333 | 59/60 | [0.911449, 0.997052] | cs-gold-v1-012 |
| Unsafe-answer rate（越低越好） | 0.0 | 0/60 | [0.0, 0.060172] | - |
| 联合质量通过率 | 0.766667 | 46/60 | [0.645637, 0.855604] | cs-gold-v1-004, cs-gold-v1-005, cs-gold-v1-006, cs-gold-v1-007, cs-gold-v1-008, cs-gold-v1-009, cs-gold-v1-012, cs-gold-v1-014, cs-gold-v1-016, cs-gold-v1-017, cs-gold-v1-018, cs-gold-v1-019, cs-gold-v1-035, cs-gold-v1-055 |

双人案件级一致：`49/60`；仲裁：`11`。

逐项标签、评论和 badcase 位于同包 `final-report.json`/`badcases.jsonl`。
