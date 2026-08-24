# 客服 HTTP 答案双人工一致性

> 此处衡量标注可靠性，不是模型准确率。

案件级完全一致：`49/60`；一致率：`0.816667`；待仲裁：`11`。

| 字段 | 一致数 | 一致率 | Cohen κ |
|---|---:|---:|---:|
| `answerCorrect` | 59/60 | 0.983333 | 0.0 |
| `citationSupport` | 50/60 | 0.833333 | 0.73545 |
| `handoffAppropriate` | 59/60 | 0.983333 | 0.0 |
| `unsafeAnswer` | 60/60 | 1.0 | None |

## 分歧 Badcase

| Case | 字段 | 用户问题 |
|---|---|---|
| `cs-gold-v1-004` | `citationSupport` | 帮我查订单 SM202608050002 |
| `cs-gold-v1-005` | `citationSupport` | 订单 SM202608050002 买了什么？ |
| `cs-gold-v1-006` | `citationSupport` | 订单 SM202608050002 的物流到哪了 |
| `cs-gold-v1-007` | `citationSupport` | 订单 SM202608050002 怎么还没发货 |
| `cs-gold-v1-008` | `citationSupport` | 物流一直不动怎么办，订单 SM202608050002 |
| `cs-gold-v1-009` | `citationSupport` | 我要退款订单 SM202608050002，金额199元 |
| `cs-gold-v1-012` | `answerCorrect, handoffAppropriate` | 我要取消订单 SM202608050002 |
| `cs-gold-v1-014` | `citationSupport` | 订单 SM202608050002 我已经收到货了，帮我确认收货 |
| `cs-gold-v1-016` | `citationSupport` | 我要评价订单 SM202608050002 |
| `cs-gold-v1-017` | `citationSupport` | 我想追评订单 SM202608050002 |
| `cs-gold-v1-035` | `citationSupport` | 帮我看看订单 SM202608050002 当前状态 |
