# AI 客服金标评测（v1）

> 状态：`HUMAN_VERIFIED`；`releaseGateEligible=false`。标签已完成独立人工复核，但结果仍是离线理解指标。
> 本报告只测客服理解/转接的核心质量证据；Agent `pass^k`、工具契约和终态门禁不计入下表。

数据集：`60` 条；SHA-256：`112dfd6ba7546b7cbad317597d944e3ab4dc02627d4ca6018733031d8eddc527`；模式：`rule`。

## 人工金标闭环

当前数据集已完成双人独立复核并冻结；以下命令仅用于复核流程复现和新版本生成。

```bash
conda activate shop
cd AI_Shop-backend/AI_Shop-agent
python -m evaluation.cli customer-service-review export --annotator reviewer-a --output /tmp/reviewer-a.open.jsonl
python -m evaluation.cli customer-service-review export --annotator reviewer-b --output /tmp/reviewer-b.open.jsonl
# 两位标注者独立填写 labels 后分别封存
python -m evaluation.cli customer-service-review seal --review /tmp/reviewer-a.open.jsonl --output /tmp/reviewer-a.sealed.jsonl
python -m evaluation.cli customer-service-review seal --review /tmp/reviewer-b.open.jsonl --output /tmp/reviewer-b.sealed.jsonl
python -m evaluation.cli customer-service-review merge --review-a /tmp/reviewer-a.sealed.jsonl --review-b /tmp/reviewer-b.sealed.jsonl --adjudication /tmp/adjudication.final.jsonl --output-dataset /tmp/customer-service-human-v1.jsonl --evidence /tmp/customer-service-human-v1.evidence.json
```

流程为 `OPEN -> SEALED -> HUMAN_VERIFIED`；sheet 带源数据/内容 SHA-256，禁止写入 `expected`、模型预测或隐藏字段，冲突必须逐 case 仲裁。当前结果虽已完成人工复核，仍是离线质量证据，且不会自动进入 release gate。

## 核心指标

| 指标 | 值 | 分子/分母 | 95% CI | badcase |
|---|---:|---:|---|---:|
| `intentMacroF1` | 0.955299 | 19.105978/20 | [0.642094, 0.92619] | 3 |
| `highRiskIntentRecall` | 1.0 | 10/10 | [0.722467, 1.0] | 0 |
| `slotEntitySpanF1` | 0.907652 | 344/414 | [0.884563, 0.961881] | 15 |
| `slotExactMatch` | 0.558824 | 19/34 | [0.394539, 0.711165] | 15 |
| `handoffRecall` | 1.0 | 14/14 | [0.784689, 1.0] | 0 |
| `criticalHandoffMissRate` | 0.0 | 0/6 | [0.0, 0.390334] | 0 |

## 生产槽位对齐诊断

以下投影只覆盖当前生产抽取器已实现的 `orderId/orderItemId/productId/productName/amount`，不替换上面的完整人工 schema 主指标。
- `canonicalSlotEntitySpanF1`：0.992785 （344/349，95% CI [0.987029, 0.99916]，badcase `cs-gold-v1-009, cs-gold-v1-020, cs-gold-v1-055, cs-gold-v1-058`）。
- `canonicalSlotExactMatch`：0.882353 （30/34，95% CI [0.733792, 0.953286]，badcase `cs-gold-v1-009, cs-gold-v1-020, cs-gold-v1-055, cs-gold-v1-058`）。
- 扩展 schema-only 案件：`0`；这些案件不应直接归因于生产 extractor 漏抽。

## 优化前后诊断（不是 A/B 或人工真值）

优化前 provisional：32 条，Intent Macro-F1 `0.849524`、高风险 Recall `0.333333`、slot EM `0.857143`、handoff Recall `0.8`；历史 badcase：cs-gold-v1-001, cs-gold-v1-002, cs-gold-v1-003, cs-gold-v1-011, cs-gold-v1-022, cs-gold-v1-026, cs-gold-v1-031, cs-gold-v1-032。
当前扩展到 60 条并完成双人复核，点估计通过参考门槛；样本量仍不足以推出行业级稳定性。
扩展切片：acknowledgement=1; after-sales=3; attribute-question=2; brand=1; budget-constraint=1; chat=1; comparison=1; compatibility=1; complaint=1; consult-search-boundary=2; critical-handoff=5; currency-slot=1; duplicate-charge=1; exchange=1; handoff=1; invoice=1; low-risk-chat=2; missing-item=1; missing-order=1; negation=1; negative-constraint=1; payment=1; payment-policy=1; payment-risk=3; policy-status-boundary=1; privacy=2; product-consult=3; product-search=3; refund=1; repeated-unresolved=1; risk-boundary=1; short-utterance=1; slot-order-id=1; slot-product-name=1; timing-question=1; unauthorized-payment=1; wrong-item=1

## Intent 明细

| Intent | support | Precision | Recall | F1 | badcase |
|---|---:|---:|---:|---:|---:|
| `ADDRESS_CHANGE` | 1 | 1.0 | 1.0 | 1.0 | 无 |
| `AFTERSALES_UNKNOWN` | 1 | 1.0 | 1.0 | 1.0 | 无 |
| `CANCEL_ORDER` | 2 | 1.0 | 1.0 | 1.0 | 无 |
| `CHAT` | 3 | 0.75 | 1.0 | 0.857143 | 无 |
| `COMPLAINT` | 3 | 1.0 | 1.0 | 1.0 | 无 |
| `CONFIRM_RECEIPT` | 1 | 1.0 | 1.0 | 1.0 | 无 |
| `DAMAGED_OR_WRONG_ITEM` | 7 | 1.0 | 1.0 | 1.0 | 无 |
| `HUMAN_REQUEST` | 6 | 1.0 | 1.0 | 1.0 | 无 |
| `INVOICE` | 2 | 1.0 | 1.0 | 1.0 | 无 |
| `PAYMENT_ISSUE` | 7 | 1.0 | 1.0 | 1.0 | 无 |
| `PRODUCT_CONSULT` | 6 | 1.0 | 0.833333 | 0.909091 | cs-gold-v1-044 |
| `PRODUCT_REVIEW` | 1 | 1.0 | 1.0 | 1.0 | 无 |
| `PRODUCT_SEARCH` | 6 | 0.857143 | 1.0 | 0.923077 | 无 |
| `QUERY_COUPON` | 1 | 1.0 | 1.0 | 1.0 | 无 |
| `QUERY_FULFILLMENT` | 1 | 1.0 | 1.0 | 1.0 | 无 |
| `QUERY_LOGISTICS` | 2 | 1.0 | 1.0 | 1.0 | 无 |
| `QUERY_ORDER` | 3 | 1.0 | 1.0 | 1.0 | 无 |
| `RECOMMENT` | 1 | 1.0 | 1.0 | 1.0 | 无 |
| `REFUND` | 5 | 1.0 | 0.6 | 0.75 | cs-gold-v1-011, cs-gold-v1-057 |
| `REFUND_STATUS` | 1 | 0.5 | 1.0 | 0.666667 | 无 |

## Badcase（逐指标）

### `cs-gold-v1-001` · slotEntitySpanF1, slotExactMatch
- 输入：我想买索尼 WH-1000XM6，预算 2000 元
- 切片/难度：`未标注` / `未标注`
- 期望：`{"intent": "PRODUCT_SEARCH", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"amount": "2000", "brand": "索尼", "budget": "2000元", "productName": "索尼 WH-1000XM6"}}`
- 实际：`{"confidence": 0.9, "entities": {"amount": "2000", "productName": "索尼 WH-1000XM6"}, "handoffReason": null, "intent": "PRODUCT_SEARCH", "nextAction": "TOOL", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-gold-v1-002` · slotEntitySpanF1, slotExactMatch
- 输入：我想买华为手机，预算 3000 元
- 切片/难度：`未标注` / `未标注`
- 期望：`{"intent": "PRODUCT_SEARCH", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"amount": "3000", "brand": "华为", "budget": "3000元", "productName": "华为手机"}}`
- 实际：`{"confidence": 0.9, "entities": {"amount": "3000", "productName": "华为手机"}, "handoffReason": null, "intent": "PRODUCT_SEARCH", "nextAction": "TOOL", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-gold-v1-003` · slotEntitySpanF1, slotExactMatch
- 输入：这款耳机支持蓝牙 5.4 吗
- 切片/难度：`未标注` / `未标注`
- 期望：`{"intent": "PRODUCT_CONSULT", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"bluetoothVersion": "5.4", "productName": "耳机"}}`
- 实际：`{"confidence": 0.9, "entities": {"productName": "耳机"}, "handoffReason": null, "intent": "PRODUCT_CONSULT", "nextAction": "ANSWER", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-gold-v1-009` · slotEntitySpanF1, slotExactMatch
- 输入：我要退款订单 SM202608050002，金额199元
- 切片/难度：`未标注` / `未标注`
- 期望：`{"intent": "REFUND", "riskLevel": "MEDIUM", "shouldHandoff": false, "slots": {"amount": "199元", "orderId": "SM202608050002"}}`
- 实际：`{"confidence": 0.9, "entities": {"amount": "199", "orderId": "SM202608050002"}, "handoffReason": null, "intent": "REFUND", "nextAction": "TOOL", "requestMode": "ACTION_PROPOSAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`SLOT_NORMALIZATION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-gold-v1-011` · intentMacroF1
- 输入：退款政策一般多久到账
- 切片/难度：`未标注` / `未标注`
- 期望：`{"intent": "REFUND", "riskLevel": "LOW", "shouldHandoff": false, "slots": {}}`
- 实际：`{"confidence": 0.96, "entities": {}, "handoffReason": null, "intent": "REFUND_STATUS", "nextAction": "TOOL", "requestMode": "INFORMATIONAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule_priority"}`
- 根因分类：`INTENT_ROUTING_OR_TAXONOMY_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-gold-v1-020` · slotEntitySpanF1, slotExactMatch
- 输入：我要开发票，订单 SM202608050002，金额199元
- 切片/难度：`未标注` / `未标注`
- 期望：`{"intent": "INVOICE", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"amount": "199元", "orderId": "SM202608050002"}}`
- 实际：`{"confidence": 0.96, "entities": {"amount": "199", "orderId": "SM202608050002"}, "handoffReason": null, "intent": "INVOICE", "nextAction": "ANSWER", "requestMode": "ACTION_PROPOSAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule_priority"}`
- 根因分类：`SLOT_NORMALIZATION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-gold-v1-029` · slotEntitySpanF1, slotExactMatch
- 输入：帮我找 500 元以内、不要户外款的男士外套
- 切片/难度：`未标注` / `未标注`
- 期望：`{"intent": "PRODUCT_SEARCH", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"amount": "500", "budget": "500元以内", "excludedStyle": "户外款", "productName": "男士外套"}}`
- 实际：`{"confidence": 0.9, "entities": {"amount": "500", "productName": "男士外套"}, "handoffReason": null, "intent": "PRODUCT_SEARCH", "nextAction": "TOOL", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-gold-v1-033` · slotEntitySpanF1, slotExactMatch
- 输入：这副耳机有没有主动降噪
- 切片/难度：`未标注` / `未标注`
- 期望：`{"intent": "PRODUCT_CONSULT", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"feature": "主动降噪", "productName": "耳机"}}`
- 实际：`{"confidence": 0.9, "entities": {"productName": "耳机"}, "handoffReason": null, "intent": "PRODUCT_CONSULT", "nextAction": "ANSWER", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-gold-v1-034` · slotEntitySpanF1, slotExactMatch
- 输入：推荐 1000 元以内的降噪耳机
- 切片/难度：`未标注` / `未标注`
- 期望：`{"intent": "PRODUCT_SEARCH", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"amount": "1000", "budget": "1000元以内", "feature": "降噪", "productName": "降噪耳机"}}`
- 实际：`{"confidence": 0.9, "entities": {"amount": "1000", "productName": "降噪耳机"}, "handoffReason": null, "intent": "PRODUCT_SEARCH", "nextAction": "TOOL", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-gold-v1-041` · slotEntitySpanF1, slotExactMatch
- 输入：这款手机续航怎么样
- 切片/难度：`product-consult, attribute-question` / `hard`
- 期望：`{"intent": "PRODUCT_CONSULT", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"feature": "续航", "productName": "手机"}}`
- 实际：`{"confidence": 0.9, "entities": {"productName": "手机"}, "handoffReason": null, "intent": "PRODUCT_CONSULT", "nextAction": "ANSWER", "requestMode": "INFORMATIONAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-gold-v1-042` · slotEntitySpanF1, slotExactMatch
- 输入：有没有适合学生的平板，预算2000元
- 切片/难度：`product-search, budget-constraint, consult-search-boundary` / `hard`
- 期望：`{"intent": "PRODUCT_SEARCH", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"amount": "2000", "audience": "学生", "budget": "2000元", "productName": "平板"}}`
- 实际：`{"confidence": 0.9, "entities": {"amount": "2000", "productName": "平板"}, "handoffReason": null, "intent": "PRODUCT_SEARCH", "nextAction": "TOOL", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-gold-v1-043` · slotEntitySpanF1, slotExactMatch
- 输入：不要苹果，推荐安卓手机
- 切片/难度：`product-search, negative-constraint, brand` / `hard`
- 期望：`{"intent": "PRODUCT_SEARCH", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"excludedBrand": "苹果", "operatingSystem": "安卓", "productName": "安卓手机"}}`
- 实际：`{"confidence": 0.9, "entities": {"productName": "安卓手机"}, "handoffReason": null, "intent": "PRODUCT_SEARCH", "nextAction": "TOOL", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-gold-v1-044` · intentMacroF1
- 输入：这款耳机和另一款相比哪个好
- 切片/难度：`product-search, comparison, consult-search-boundary` / `hard`
- 期望：`{"intent": "PRODUCT_CONSULT", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"productName": "耳机"}}`
- 实际：`{"confidence": 0.9, "entities": {"productName": "耳机"}, "handoffReason": null, "intent": "PRODUCT_SEARCH", "nextAction": "TOOL", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`INTENT_ROUTING_OR_TAXONOMY_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-gold-v1-045` · slotEntitySpanF1, slotExactMatch
- 输入：耳机有主动降噪嘛
- 切片/难度：`product-consult, attribute-question` / `hard`
- 期望：`{"intent": "PRODUCT_CONSULT", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"feature": "主动降噪", "productName": "耳机"}}`
- 实际：`{"confidence": 0.9, "entities": {"productName": "耳机"}, "handoffReason": null, "intent": "PRODUCT_CONSULT", "nextAction": "ANSWER", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-gold-v1-055` · slotEntitySpanF1, slotExactMatch
- 输入：少了一个配件，订单SM202608050002
- 切片/难度：`after-sales, missing-item, slot-order-id` / `hard`
- 期望：`{"intent": "DAMAGED_OR_WRONG_ITEM", "riskLevel": "MEDIUM", "shouldHandoff": false, "slots": {"orderId": "SM202608050002", "productName": "配件", "quantity": "1"}}`
- 实际：`{"confidence": 0.96, "entities": {"orderId": "SM202608050002"}, "handoffReason": null, "intent": "DAMAGED_OR_WRONG_ITEM", "nextAction": "ANSWER", "requestMode": "INFORMATIONAL", "riskLevel": "MEDIUM", "shouldHandoff": false, "source": "rule_priority"}`
- 根因分类：`SLOT_EXTRACTION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-gold-v1-057` · intentMacroF1
- 输入：退款多久到账呀
- 切片/难度：`refund, timing-question, policy-status-boundary` / `hard`
- 期望：`{"intent": "REFUND", "riskLevel": "LOW", "shouldHandoff": false, "slots": {}}`
- 实际：`{"confidence": 0.9, "entities": {}, "handoffReason": null, "intent": "CHAT", "nextAction": "ANSWER", "requestMode": "INFORMATIONAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`INTENT_ROUTING_OR_TAXONOMY_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-gold-v1-058` · slotEntitySpanF1, slotExactMatch
- 输入：¥199.00的订单我要开发票
- 切片/难度：`invoice, currency-slot` / `medium`
- 期望：`{"intent": "INVOICE", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"amount": "¥199.00"}}`
- 实际：`{"confidence": 0.96, "entities": {"amount": "199.00"}, "handoffReason": null, "intent": "INVOICE", "nextAction": "ANSWER", "requestMode": "ACTION_PROPOSAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule_priority"}`
- 根因分类：`SLOT_EXTRACTION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-gold-v1-059` · slotEntitySpanF1, slotExactMatch
- 输入：手机壳有没有适配 iPhone 15
- 切片/难度：`product-consult, compatibility, slot-product-name` / `hard`
- 期望：`{"intent": "PRODUCT_CONSULT", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"compatibleModel": "iPhone 15", "productName": "手机壳"}}`
- 实际：`{"confidence": 0.9, "entities": {"productName": "手机壳"}, "handoffReason": null, "intent": "PRODUCT_CONSULT", "nextAction": "ANSWER", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED`；标签已冻结，需将该 case 纳入对应回归切片。

## 口径与限制

- 人工金标冻结流程已完成：两名标注者盲标 intent/risk/转人工/严重度/slot，并完成冲突仲裁；当前标签版本可复核，但仍不代表线上客服成功率。
- 高风险 Recall 的正类是独立标签 `riskLevel=HIGH`，不是模型自报风险；严重漏转人工只统计 `handoffSeverity=CRITICAL`。
- slot Entity/Span F1 使用 NFKC 后的字符 span；`slotExactMatch` 只在存在 gold slot 的请求上计分，空 slot 不抬高结果。
- `HANDOFF_SUGGESTED` 不算即时转人工成功；远程结果未知、Provider 失败和人工校准不在本基线中伪造。
- 本版本人工复核已完成；后续修订必须生成新数据集版本并保留当前包，不得覆盖历史结果。
