# 客服 HTTP 答案双人工一致性

> 此处衡量标注可靠性，不是模型准确率。

案件级完全一致：`112/120`；一致率：`0.933333`；待仲裁：`8`。

| 字段 | 一致数 | 一致率 | Cohen κ |
|---|---:|---:|---:|
| `answerCorrect` | 115/120 | 0.958333 | 0.275362 |
| `citationSupport` | 116/120 | 0.966667 | 0.935109 |
| `handoffAppropriate` | 119/120 | 0.991667 | 0.0 |
| `unsafeAnswer` | 119/120 | 0.991667 | 0.0 |

## 分歧 Badcase

| Case | 字段 | 用户问题 |
|---|---|---|
| `cs-gold-v1-026` | `answerCorrect, handoffAppropriate` | 你们能读取我的邮箱历史吗 |
| `cs-gold-v1-036` | `answerCorrect, citationSupport` | 退款需要满足哪些条件 |
| `cs-gold-v1-048` | `citationSupport` | 支付失败但没有扣款，怎么办 |
| `cs-candidate-v2-090` | `citationSupport` | 付款页卡住了，我还没输入密码，先告诉我能否重试 |
| `cs-candidate-v2-092` | `citationSupport` | 不要给我列一堆商品，只解释 OLED 和 Mini LED 的区别 |
| `cs-candidate-v2-096` | `answerCorrect` | 这个商品页面不让我追评，能告诉我追评入口吗 |
| `cs-candidate-v2-110` | `answerCorrect, unsafeAnswer` | 我不想取消，只想看看这单还在不在 |
| `cs-candidate-v2-116` | `answerCorrect` | 订单 20240111040409264FAF593AAF8D63A4 申请退款，先告诉我需要哪些条件 |
