# AI_Shop 文档与证据入口

文档按内容归档，不按日期复制新文件。后续同类变化在同一份稳定文档的“增量记录”中追加；日期只用于说明开发阶段。

| 目录/文件 | 内容 | 证据等级 |
|---|---|---|
| [project/AI_Shop主线与开发记录](project/AI_Shop主线与开发记录.md) | AI 主线、方法判断、关键改造、阶段性取舍和后续路线 | 源码与运行证据汇总 |
| [evaluation/AI质量评测与Badcase](evaluation/AI质量评测与Badcase.md) | Search/RAG/Agent 质量指标、门禁、badcase、性能和限制 | current/archive 证据包投影 |
| [evaluation/AI质量评测与Badcase.json](evaluation/AI质量评测与Badcase.json) | Search 指标及逐指标 badcase 的机器可读结果 | immutable scorecard 投影 |
| [evaluation/customer-service/客服金标评测](evaluation/customer-service/客服金标评测.md) | intent、风险、slot、handoff 四项客服理解证据 | provisional，未人工复核 |
| [evaluation/customer-service/客服金标评测.json](evaluation/customer-service/客服金标评测.json) | 客服评测逐 case 预测和 badcase | provisional 机器证据 |
| [evidence-manifest.json](evidence-manifest.json) | current、archive、visible run、哈希和生命周期机器索引 | 机器校验入口 |

项目根目录的 [Java 后端面试报告](../AI应用开发_Java后端_真实面试题与备考报告_20260821.md) 是独立的求职研究材料，不与项目开发日志重复合并。

## 校验与重跑

Python 评测统一使用 Conda `shop` 环境，并在 Agent 目录执行：

```bash
conda activate shop
cd AI_Shop-backend/AI_Shop-agent
python -m evaluation.cli validate
python -m evaluation.cli customer-service-gold --mode rule
```

客服金标完成独立人工复核后，先冻结标签版本，再重新运行并保留本 provisional 结果；不得把规则基线、模型自评或 `pass^k` 当成人工真值。
