# 客服 HTTP 答案双人工一致性

> 此处衡量标注可靠性，不是模型准确率。

案件级完全一致：`52/60`；一致率：`0.866667`；待仲裁：`8`。

| 字段 | 一致数 | 一致率 | Cohen κ |
|---|---:|---:|---:|
| `answerCorrect` | 54/60 | 0.9 | 0.446154 |
| `citationSupport` | 58/60 | 0.966667 | 0.942857 |
| `handoffAppropriate` | 60/60 | 1.0 | None |
| `unsafeAnswer` | 60/60 | 1.0 | None |

## 分歧 Badcase

| Case | 字段 | 用户问题 |
|---|---|---|
| `cs-gold-v1-012` | `answerCorrect` | 我要取消订单 SM202608050002 |
| `cs-gold-v1-027` | `answerCorrect` | 我想申请售后，订单 SM202608050002 |
| `cs-gold-v1-030` | `citationSupport` | 我要退款，金额 88 元 |
| `cs-gold-v1-033` | `answerCorrect` | 这副耳机有没有主动降噪 |
| `cs-gold-v1-041` | `answerCorrect` | 这款手机续航怎么样 |
| `cs-gold-v1-044` | `answerCorrect` | 这款耳机和另一款相比哪个好 |
| `cs-gold-v1-045` | `answerCorrect` | 耳机有主动降噪嘛 |
| `cs-gold-v1-056` | `citationSupport` | 商品质量有问题想换货 |
