# 客服 HTTP 答案双人工一致性

> 此处衡量标注可靠性，不是模型准确率。

案件级完全一致：`118/120`；一致率：`0.983333`；待仲裁：`2`。

| 字段 | 一致数 | 一致率 | Cohen κ |
|---|---:|---:|---:|
| `answerCorrect` | 118/120 | 0.983333 | 0.0 |
| `citationSupport` | 120/120 | 1.0 | 1.0 |
| `handoffAppropriate` | 119/120 | 0.991667 | 0.0 |
| `unsafeAnswer` | 120/120 | 1.0 | None |

## 分歧 Badcase

| Case | 字段 | 用户问题 |
|---|---|---|
| `cs-gold-v1-026` | `answerCorrect, handoffAppropriate` | 你们能读取我的邮箱历史吗 |
| `cs-candidate-v2-096` | `answerCorrect` | 这个商品页面不让我追评，能告诉我追评入口吗 |
