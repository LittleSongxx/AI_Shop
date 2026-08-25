# 客服 HTTP 答案双人工一致性

> 此处衡量标注可靠性，不是模型准确率。

案件级完全一致：`56/60`；一致率：`0.933333`；待仲裁：`4`。

| 字段 | 一致数 | 一致率 | Cohen κ |
|---|---:|---:|---:|
| `answerCorrect` | 57/60 | 0.95 | 0.383562 |
| `citationSupport` | 59/60 | 0.983333 | 0.973498 |
| `handoffAppropriate` | 60/60 | 1.0 | None |
| `unsafeAnswer` | 59/60 | 0.983333 | 0.0 |

## 分歧 Badcase

| Case | 字段 | 用户问题 |
|---|---|---|
| `cs-gold-v1-014` | `answerCorrect, unsafeAnswer` | 订单 20251116015041302F19C092ED2FAC8F 我已经收到货了，帮我确认收货 |
| `cs-gold-v1-029` | `citationSupport` | 帮我找 500 元以内、不要户外款的男士外套 |
| `cs-gold-v1-043` | `answerCorrect` | 不要苹果，推荐安卓手机 |
| `cs-gold-v1-059` | `answerCorrect` | 手机壳有没有适配 iPhone 15 |
