# 客服双人工一致性证据

> 状态：`PENDING_ADJUDICATION`；此报告只描述标注可靠性，不是模型准确率，也不进入 release gate。

数据集 SHA-256：`7c0b874ec9ec72657ec4726bb8cbfc837a22dee87b3c2e2507cddbb003ec85dd`；案件数：`60`；
案件级完全一致：`35/60`；
案件级一致率：`0.5833333333333334`。

## 字段一致性

| 字段 | 一致数 | 一致率 | Cohen κ |
|---|---:|---:|---:|
| `intent` | 57/60 | 0.95 | 0.9459134615384615 |
| `riskLevel` | 56/60 | 0.9333333333333333 | 0.8899082568807339 |
| `shouldHandoff` | 60/60 | 1.0 | 1.0 |
| `handoffSeverity` | 56/60 | 0.9333333333333333 | 0.8255813953488372 |
| `slots` | 45/60 | 0.75 | 0.6620352985354864 |

## 槽位诊断

- key-set 一致：`46/60`（0.7666666666666667）。
- 两位标注者共同填写的槽位值一致：`24/26`（0.9230769230769231）。
- 当前 raw slot 一致率受 `budget/amount`、`brand/productName` 等未冻结的槽位 taxonomy 影响；仲裁前不能直接作为模型 Slot F1。

## 冲突 Badcase

| Case | 冲突字段 | 用户原话 |
|---|---|---|
| `cs-gold-v1-001` | `slots` | 我想买索尼 WH-1000XM6，预算 2000 元 |
| `cs-gold-v1-002` | `slots` | 我想买华为手机，预算 3000 元 |
| `cs-gold-v1-003` | `slots` | 这款耳机支持蓝牙 5.4 吗 |
| `cs-gold-v1-007` | `riskLevel` | 订单 SM202608050002 怎么还没发货 |
| `cs-gold-v1-008` | `riskLevel` | 物流一直不动怎么办，订单 SM202608050002 |
| `cs-gold-v1-014` | `riskLevel` | 订单 SM202608050002 我已经收到货了，帮我确认收货 |
| `cs-gold-v1-025` | `handoffSeverity` | 支付失败了但是钱扣了 |
| `cs-gold-v1-029` | `slots` | 帮我找 500 元以内、不要户外款的男士外套 |
| `cs-gold-v1-030` | `slots` | 我要退款，金额 88 元 |
| `cs-gold-v1-033` | `slots` | 这副耳机有没有主动降噪 |
| `cs-gold-v1-034` | `slots` | 推荐 1000 元以内的降噪耳机 |
| `cs-gold-v1-037` | `handoffSeverity` | 订单 SM202608050002 重复支付了两次 |
| `cs-gold-v1-039` | `slots` | 收到的耳机碎了 |
| `cs-gold-v1-041` | `slots` | 这款手机续航怎么样 |
| `cs-gold-v1-042` | `slots` | 有没有适合学生的平板，预算2000元 |
| `cs-gold-v1-043` | `slots` | 不要苹果，推荐安卓手机 |
| `cs-gold-v1-044` | `slots` | 这款耳机和另一款相比哪个好 |
| `cs-gold-v1-045` | `slots` | 耳机有主动降噪嘛 |
| `cs-gold-v1-046` | `handoffSeverity` | 银行卡被扣了两次 |
| `cs-gold-v1-047` | `handoffSeverity` | 付款成功但订单没生成 |
| `cs-gold-v1-049` | `intent` | 支付方式有哪些 |
| `cs-gold-v1-055` | `slots` | 少了一个配件，订单SM202608050002 |
| `cs-gold-v1-056` | `intent` | 商品质量有问题想换货 |
| `cs-gold-v1-057` | `intent, riskLevel` | 退款多久到账呀 |
| `cs-gold-v1-059` | `slots` | 手机壳有没有适配 iPhone 15 |

## 下一步

1. 先冻结 core slot taxonomy（建议先覆盖生产已支持的 `orderId`、`amount`、`productName`、`orderItemId`、`productId`）。
2. 由 lead reviewer 只仲裁冲突 case，并在 adjudication JSONL 写明理由；未完成前禁止生成 `HUMAN_VERIFIED` 数据集。
