# 客服 HTTP 答案人工质量证据

> `HUMAN_REVIEWED_ADJUDICATED`；样本 `60`；不是线上 CSAT/FCR。

| 指标 | 数值 | 分子/分母 | 95% CI | badcase |
|---|---:|---:|---|---|
| 答案正确率 | 0.85 | 51/60 | [0.738854, 0.919026] | cs-gold-v1-003, cs-gold-v1-012, cs-gold-v1-027, cs-gold-v1-029, cs-gold-v1-033, cs-gold-v1-041, cs-gold-v1-044, cs-gold-v1-045, cs-gold-v1-059 |
| 引用语义支持率 | 0.2 | 6/30 | [0.095051, 0.373057] | cs-gold-v1-001, cs-gold-v1-002, cs-gold-v1-004, cs-gold-v1-005, cs-gold-v1-006, cs-gold-v1-009, cs-gold-v1-010, cs-gold-v1-014, cs-gold-v1-015, cs-gold-v1-016, cs-gold-v1-017, cs-gold-v1-018, cs-gold-v1-021, cs-gold-v1-029, cs-gold-v1-030, cs-gold-v1-033, cs-gold-v1-034, cs-gold-v1-035, cs-gold-v1-041, cs-gold-v1-042, cs-gold-v1-043, cs-gold-v1-045, cs-gold-v1-056, cs-gold-v1-059 |
| 转人工适当率 | 1.0 | 60/60 | [0.939828, 1.0] | - |
| Unsafe-answer rate（越低越好） | 0.0 | 0/60 | [0.0, 0.060172] | - |
| 联合质量通过率 | 0.533333 | 32/60 | [0.408934, 0.653721] | cs-gold-v1-001, cs-gold-v1-002, cs-gold-v1-003, cs-gold-v1-004, cs-gold-v1-005, cs-gold-v1-006, cs-gold-v1-009, cs-gold-v1-010, cs-gold-v1-012, cs-gold-v1-014, cs-gold-v1-015, cs-gold-v1-016, cs-gold-v1-017, cs-gold-v1-018, cs-gold-v1-021, cs-gold-v1-027, cs-gold-v1-029, cs-gold-v1-030, cs-gold-v1-033, cs-gold-v1-034, cs-gold-v1-035, cs-gold-v1-041, cs-gold-v1-042, cs-gold-v1-043, cs-gold-v1-044, cs-gold-v1-045, cs-gold-v1-056, cs-gold-v1-059 |

双人案件级一致：`52/60`；仲裁：`8`。

逐项标签、评论和 badcase 位于同包 `final-report.json`/`badcases.jsonl`。
