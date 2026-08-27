# AI-Shop 人工评审协调包（第一轮，2026-08-26）

本目录是协调人母包，不应原样发给任何一位 reviewer，因为其中同时包含 A/B 文件、输入标签任务和模型答案任务。请分别发送同目录外生成的标签 A/B、答案 A/B 和来源独立复核 ZIP；不要把标签表与答案表合在同一个 reviewer 包中。

## 本轮可立即填写

1. `01-label-policy-v2.1/`：25 条输入标签政策重标，两位独立 reviewer 各一份。
2. `02-answer-quality-v43/`：120 条冻结答案质量评审，两位独立 reviewer 各一份。
3. `03-provenance-independent-reaudit/`：12 条历史来源独立复核，必须由第三类独立人员完成。

每位 reviewer 只能编辑分配表中的 `labels`、`comment`，以及复核表明确要求的 `reviewerId`；manifest 与其他字段不改。`ORIGINAL-SHA256SUMS` 绑定的是交付时空白状态，填写后表 hash 改变是预期行为，之后由项目方校验并 seal。

## 当前不能填写

- `04-adjudication-after-sealing/` 只有方法，没有样本。真实仲裁表必须在 A/B 均完成、seal 并比较后，仅从分歧集生成。
- 当前 125 条 external candidate 已对开发者可见，永久不具备 unseen 资格；`05-.../` 只有替代批次规范，不把这 125 条伪装成本轮正式人工任务。

## 角色要求

- 标签 A/B 不得看到当前 gold、任何模型输出或仲裁上下文；答案 A/B 不得看到旧答案标签、模型自评或另一人的结果。
- 同一个人如确需承担标签与答案两类任务，必须先完成并 seal 标签表，再取得答案包；更推荐使用不同人员。
- 来源独立复核者应与历史数据/模型开发、旧 A/B、当前 v2.1 A/B 都不同。
- 仲裁者在 A/B seal 前不得看中间结果，且不能由 A/B 自己兼任。

回传时保留原文件名和同名 manifest。项目方会在 `shop` conda 环境完成来源校验、封存、比较、仲裁模板导出和 successor evidence 构建。
