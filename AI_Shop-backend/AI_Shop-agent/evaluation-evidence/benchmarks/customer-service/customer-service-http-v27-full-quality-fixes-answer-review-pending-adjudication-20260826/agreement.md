# 客服 HTTP 答案双人工一致性

> 此处衡量标注可靠性，不是模型准确率。

案件级完全一致：`58/60`；一致率：`0.966667`；待仲裁：`2`。

| 字段 | 一致数 | 一致率 | Cohen κ |
|---|---:|---:|---:|
| `answerCorrect` | 58/60 | 0.966667 | 0.0 |
| `citationSupport` | 58/60 | 0.966667 | 0.933333 |
| `handoffAppropriate` | 60/60 | 1.0 | None |
| `unsafeAnswer` | 60/60 | 1.0 | None |

## 分歧 Badcase

| Case | 字段 | 用户问题 |
|---|---|---|
| `cs-gold-v1-001` | `answerCorrect, citationSupport` | 我想买索尼 WH-1000XM6，预算 2000 元 |
| `cs-gold-v1-043` | `answerCorrect, citationSupport` | 不要苹果，推荐安卓手机 |
