# 客服双人工一致性证据

> 状态：`PENDING_ADJUDICATION`；此报告只描述标注可靠性，不是模型准确率，也不进入 release gate。

数据集 SHA-256：`7110835813e32f6ed3823bd22c2e1487f7e9c2c62bb31d4eac92fb61f9bf4353`；案件数：`60`；
案件级完全一致：`34/60`；
案件级一致率：`0.5666666666666667`。

## 字段一致性

| 字段 | 一致数 | 一致率 | Cohen κ |
|---|---:|---:|---:|
| `intent` | 52/60 | 0.8666666666666667 | 0.8596491228070176 |
| `riskLevel` | 58/60 | 0.9666666666666667 | 0.9418040737148398 |
| `shouldHandoff` | 57/60 | 0.95 | 0.8793565683646112 |
| `handoffSeverity` | 57/60 | 0.95 | 0.8886827458256029 |
| `slots` | 45/60 | 0.75 | 0.6927278934790031 |

## 槽位诊断

- key-set 一致：`49/60`（0.8166666666666667）。
- 两位标注者共同填写的槽位值一致：`38/43`（0.8837209302325582）。
- 当前 raw slot 一致率受 `budget/amount`、`brand/productName` 等未冻结的槽位 taxonomy 影响；仲裁前不能直接作为模型 Slot F1。

## 冲突 Badcase

| Case | 冲突字段 | 用户原话 |
|---|---|---|
| `cs-candidate-v2-062` | `shouldHandoff, handoffSeverity` | 我不是要取消订单，只想把 20260823002 的地址换一下 |
| `cs-candidate-v2-063` | `shouldHandoff, handoffSeverity, slots` | 包裹已经出库了还能改到公司地址吗 |
| `cs-candidate-v2-066` | `intent` | 售后入口一直让我重选问题，转个人帮我看吧 |
| `cs-candidate-v2-069` | `slots` | 这个订单误下了两次，帮我取消其中一单，拿不准就转人工 |
| `cs-candidate-v2-073` | `slots` | 客服连续三次答非所问，我要投诉这次服务 |
| `cs-candidate-v2-074` | `intent, slots` | 你们把我的收货地址发给陌生人了，立刻转安全专员 |
| `cs-candidate-v2-079` | `slots` | 收到的蓝色耳机变成红色了，订单 20260823005 |
| `cs-candidate-v2-080` | `intent` | 充电宝鼓包而且发烫，我已经停止使用，马上转人工 |
| `cs-candidate-v2-081` | `shouldHandoff, handoffSeverity` | 一箱应该有 12 瓶但只到 10 瓶，不是要退款，先查漏发 |
| `cs-candidate-v2-083` | `riskLevel` | 我不是要机器人继续解释，找真人处理 |
| `cs-candidate-v2-086` | `slots` | ￥１，２９９．００ 这笔能开公司抬头吗 |
| `cs-candidate-v2-087` | `intent` | 发票金额错了，不是咨询开票流程，帮我转人工更正 |
| `cs-candidate-v2-093` | `slots` | 这双鞋适合雨天通勤吗 |
| `cs-candidate-v2-094` | `slots` | 订单商品已经用了一周，我想写个五星评价 |
| `cs-candidate-v2-097` | `slots` | 找 3000 元以内的索尼降噪耳机，不要入耳式 |
| `cs-candidate-v2-099` | `slots` | 预算一千二，想买个能拍照的手机，有合适的吗 |
| `cs-candidate-v2-102` | `intent` | 券显示已使用但我没下单，转人工核对一下 |
| `cs-candidate-v2-103` | `slots` | 这件预售商品大概几天能发货 |
| `cs-candidate-v2-105` | `slots` | 承诺今天发货但还没出库，订单 20260823006 |
| `cs-candidate-v2-108` | `intent` | 物流显示签收但我没收到，马上转人工查凭证 |
| `cs-candidate-v2-111` | `riskLevel` | 订单列表突然少了一笔 1,299 元的订单 |
| `cs-candidate-v2-112` | `slots` | 刚才推荐的太贵了，换一批便宜点的 |
| `cs-candidate-v2-113` | `slots` | 不要苹果，再推荐两款安卓旗舰 |
| `cs-candidate-v2-115` | `slots` | 我想了解七天无理由退款多久到账，不是查某一笔进度 |
| `cs-candidate-v2-117` | `intent` | 不要把钱退到陌生卡里，我要人工确认退款账户 |
| `cs-candidate-v2-120` | `intent` | 退款页面说成功但银行卡没有入账，请转人工核账 |

## 下一步

1. 先冻结 core slot taxonomy（建议先覆盖生产已支持的 `orderId`、`amount`、`productName`、`orderItemId`、`productId`）。
2. 由 lead reviewer 只仲裁冲突 case，并在 adjudication JSONL 写明理由；未完成前禁止生成 `HUMAN_VERIFIED` 数据集。
