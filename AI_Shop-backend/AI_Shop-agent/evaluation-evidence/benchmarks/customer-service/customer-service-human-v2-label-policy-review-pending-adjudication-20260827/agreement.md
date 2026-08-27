# 客服 v2.1 标签政策双盲一致性

> 此处衡量标注可靠性，不是系统准确率。

案件级完全一致：`20/25`；一致率：`0.8`；待仲裁：`5`。

| 字段 | 一致数 | 一致率 | Cohen κ |
|---|---:|---:|---:|
| `intent` | 25/25 | 1.0 | 1.0 |
| `riskLevel` | 25/25 | 1.0 | 1.0 |
| `shouldHandoff` | 23/25 | 0.92 | 0.626866 |
| `handoffSeverity` | 23/25 | 0.92 | 0.637681 |
| `slots` | 22/25 | 0.88 | 0.871355 |

## 分歧

| Case | 字段 | 用户问题 |
|---|---|---|
| `cs-candidate-v2-073` | `shouldHandoff, handoffSeverity` | 客服连续三次答非所问，我要投诉这次服务 |
| `cs-candidate-v2-112` | `slots` | 刚才推荐的太贵了，换一批便宜点的 |
| `cs-candidate-v2-113` | `slots` | 不要苹果，再推荐两款安卓旗舰 |
| `cs-gold-v1-029` | `slots` | 帮我找 500 元以内、不要户外款的男士外套 |
| `cs-gold-v1-052` | `shouldHandoff, handoffSeverity` | 我已经问了三次还没解决 |
