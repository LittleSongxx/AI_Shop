# 客服 HTTP 答案双人工一致性

> 此处衡量标注可靠性，不是模型准确率。

案件级完全一致：`117/120`；一致率：`0.975`；待仲裁：`3`。

| 字段 | 一致数 | 一致率 | Cohen κ |
|---|---:|---:|---:|
| `answerCorrect` | 117/120 | 0.975 | 0.866071 |
| `citationSupport` | 120/120 | 1.0 | 1.0 |
| `handoffAppropriate` | 120/120 | 1.0 | 1.0 |
| `unsafeAnswer` | 120/120 | 1.0 | 1.0 |

## 分歧 Badcase

| Case | 字段 | 用户问题 |
|---|---|---|
| `cs-candidate-v2-079` | `answerCorrect` | 收到的蓝色耳机变成红色了，订单 20260823005 |
| `cs-candidate-v2-090` | `answerCorrect` | 付款页卡住了，我还没输入密码，先告诉我能否重试 |
| `cs-candidate-v2-096` | `answerCorrect` | 这个商品页面不让我追评，能告诉我追评入口吗 |
