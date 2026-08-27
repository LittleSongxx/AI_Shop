# AI 客服输入理解金标评测

> 状态：`HUMAN_VERIFIED`；`releaseGateEligible=false`。标签已完成独立人工复核，但结果仍是离线理解指标。
> 本报告只测客服理解/转接的核心质量证据；Agent `pass^k`、工具契约和终态门禁不计入下表。

数据集：`120` 条；SHA-256：`ab5129a73cf6f986173d92e3f5f04ab7e8689bae9ad4c7d7294fa13b587ee079`；模式：`rule`。

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
| `intentMacroF1` | 0.71724 | 14.344801/20 | [0.623179, 0.779252] | 31 |
| `highRiskIntentRecall` | 0.733333 | 11/15 | [0.480496, 0.891025] | 4 |
| `slotEntitySpanF1` | 0.77392 | 914/1181 | [0.690415, 0.845368] | 38 |
| `slotExactMatch` | 0.457143 | 32/70 | [0.345727, 0.573017] | 38 |
| `handoffRecall` | 0.6875 | 22/32 | [0.514333, 0.820475] | 10 |
| `criticalHandoffMissRate` | 0.272727 | 3/11 | [0.097461, 0.565645] | 3 |

## 证据版本边界

- 当前 120 条规则预路由结果绑定本报告数据集；它是输入理解/路由诊断，不是 HTTP 最终答案质量或新 holdout 泛化结果。
- HTTP 最终答案另有独立 `HUMAN_REVIEWED_ADJUDICATED` 证据包；固定旧回放的引用语义支持为 `6/30 eligible`（20.0%），不能从本报告的意图/槽位结果推导生成答案质量。
- HTTP 新输出必须重新双人盲审；旧答案 labels 绑定 source run 和答案 SHA-256，不能迁移到新代码结果。

## 生产槽位对齐诊断

以下投影只覆盖当前生产抽取器已实现的 `orderId/orderItemId/productId/productName/amount`，不替换上面的完整人工 schema 主指标。
- `canonicalSlotEntitySpanF1`：0.824919 （768/931，95% CI [0.737104, 0.895277]，badcase `cs-gold-v1-009, cs-gold-v1-020, cs-gold-v1-058, cs-candidate-v2-061, cs-candidate-v2-062, cs-candidate-v2-067, cs-candidate-v2-076, cs-candidate-v2-079, cs-candidate-v2-080, cs-candidate-v2-085, cs-candidate-v2-086, cs-candidate-v2-088, cs-candidate-v2-089, cs-candidate-v2-091, cs-candidate-v2-097, cs-candidate-v2-098, cs-candidate-v2-099, cs-candidate-v2-103, cs-candidate-v2-105, cs-candidate-v2-106, cs-candidate-v2-109, cs-candidate-v2-111, cs-candidate-v2-116, cs-candidate-v2-119`）。
- `canonicalSlotExactMatch`：0.578947 （33/57，95% CI [0.449801, 0.698124]，badcase `cs-gold-v1-009, cs-gold-v1-020, cs-gold-v1-058, cs-candidate-v2-061, cs-candidate-v2-062, cs-candidate-v2-067, cs-candidate-v2-076, cs-candidate-v2-079, cs-candidate-v2-080, cs-candidate-v2-085, cs-candidate-v2-086, cs-candidate-v2-088, cs-candidate-v2-089, cs-candidate-v2-091, cs-candidate-v2-097, cs-candidate-v2-098, cs-candidate-v2-099, cs-candidate-v2-103, cs-candidate-v2-105, cs-candidate-v2-106, cs-candidate-v2-109, cs-candidate-v2-111, cs-candidate-v2-116, cs-candidate-v2-119`）。
- 扩展 schema-only 案件：`13`；这些案件不应直接归因于生产 extractor 漏抽。

## 优化前后诊断（不是 A/B 或人工真值）

优化前 provisional：32 条，Intent Macro-F1 `0.849524`、高风险 Recall `0.333333`、slot EM `0.857143`、handoff Recall `0.8`；历史 badcase：cs-gold-v1-001, cs-gold-v1-002, cs-gold-v1-003, cs-gold-v1-011, cs-gold-v1-022, cs-gold-v1-026, cs-gold-v1-031, cs-gold-v1-032。
当前 120 条点估计未通过参考门槛：intentMacroF1, highRiskIntentRecall, slotEntitySpanF1, slotExactMatch, handoffRecall, criticalHandoffMissRate；这些门槛不是统一行业标准，也不能替代人工答案质量。
样本量、切片覆盖和评测模式仍不足以推出行业级稳定性或线上质量。
扩展切片：account-compromise=1; account-safety=1; acknowledgement=2; address-change=3; after-sales=3; aftersales-boundary=1; aftersales-unknown=3; ambiguous=1; amount-mismatch=1; amount-slot=1; attribute-question=2; battery-safety=1; brand=1; budget=2; budget-constraint=1; cancel-negation=1; cancel-order=3; canonical-slot=5; chat=4; chinese-number=1; comparison=2; compatibility=1; complaint=4; complaint-negation=1; conditional-handoff=1; confirm-receipt=3; consult-negation=1; consult-search-boundary=3; contextual=1; critical-handoff=14; currency-slot=1; currency-wording=1; damaged-item=1; deadline=1; discount-boundary=1; duplicate-charge=2; duplicate-order=1; elliptical=2; elliptical-product=1; exchange=1; explicit-handoff=3; false-delivery=1; follow-up-review=1; fulfillment-boundary=1; full-width-currency=1; handoff=4; human-request=3; intent-boundary=2; invoice=4; logistics-boundary=2; low-risk-chat=2; missing-item=2; missing-order=1; missing-record=1; negated-handoff=2; negated-write=1; negation=7; negative-brand=1; negative-constraint=2; no-secret=1; order-id=8; out-of-domain=1; payment=1; payment-issue=3; payment-policy=1; payment-risk=3; pending=1; policy-before-action=1; policy-negation=1; policy-question=4; policy-status-boundary=1; positive-review=1; post-dispatch=1; presale=1; price-feedback=1; privacy=3; product-consult=6; product-name=2; product-review=3; product-search=6; quantity=3; query-coupon=3; query-fulfillment=3; query-logistics=3; query-order=3; recomment=3; refund=4; refund-negation=1; refund-status=3; refund-status-boundary=1; repeated-unresolved=1; retry-question=1; risk-boundary=1; search-negation=1; service-quality=1; short-utterance=1; slot-order-id=1; slot-product-name=1; stale-tracking=1; state-conflict=2; terminology=1; threshold=1; time-wording=2; timing-question=1; unauthorized-payment=1; underspecified=1; unknown-outcome=1; use-case=1; use-case-feedback=1; write-intent=2; wrong-item=2

## Intent 明细

| Intent | support | Precision | Recall | F1 | badcase |
|---|---:|---:|---:|---:|---:|
| `ADDRESS_CHANGE` | 4 | 1.0 | 0.75 | 0.857143 | cs-candidate-v2-061 |
| `AFTERSALES_UNKNOWN` | 3 | 0.5 | 0.666667 | 0.571429 | cs-candidate-v2-065 |
| `CANCEL_ORDER` | 5 | 0.75 | 0.6 | 0.666667 | cs-candidate-v2-067, cs-candidate-v2-069 |
| `CHAT` | 6 | 0.3 | 1.0 | 0.461538 | 无 |
| `COMPLAINT` | 5 | 0.8 | 0.8 | 0.8 | cs-candidate-v2-075 |
| `CONFIRM_RECEIPT` | 4 | 1.0 | 0.75 | 0.857143 | cs-candidate-v2-078 |
| `DAMAGED_OR_WRONG_ITEM` | 9 | 0.888889 | 0.888889 | 0.888889 | cs-candidate-v2-079 |
| `HUMAN_REQUEST` | 17 | 0.923077 | 0.705882 | 0.8 | cs-candidate-v2-066, cs-candidate-v2-074, cs-candidate-v2-083, cs-candidate-v2-084, cs-candidate-v2-117 |
| `INVOICE` | 4 | 1.0 | 0.75 | 0.857143 | cs-candidate-v2-085 |
| `PAYMENT_ISSUE` | 10 | 1.0 | 0.8 | 0.888889 | cs-candidate-v2-089, cs-candidate-v2-090 |
| `PRODUCT_CONSULT` | 9 | 1.0 | 0.666667 | 0.8 | cs-candidate-v2-091, cs-candidate-v2-092, cs-candidate-v2-093 |
| `PRODUCT_REVIEW` | 4 | 1.0 | 0.5 | 0.666667 | cs-candidate-v2-095, cs-candidate-v2-096 |
| `PRODUCT_SEARCH` | 9 | 0.692308 | 1.0 | 0.818182 | 无 |
| `QUERY_COUPON` | 3 | 1.0 | 0.666667 | 0.8 | cs-candidate-v2-100 |
| `QUERY_FULFILLMENT` | 4 | 1.0 | 0.25 | 0.4 | cs-candidate-v2-103, cs-candidate-v2-104, cs-candidate-v2-105 |
| `QUERY_LOGISTICS` | 4 | 0.666667 | 1.0 | 0.8 | 无 |
| `QUERY_ORDER` | 6 | 1.0 | 0.666667 | 0.8 | cs-candidate-v2-110, cs-candidate-v2-111 |
| `RECOMMENT` | 4 | 0.5 | 0.25 | 0.333333 | cs-candidate-v2-112, cs-candidate-v2-113, cs-candidate-v2-114 |
| `REFUND` | 7 | 0.636364 | 1.0 | 0.777778 | 无 |
| `REFUND_STATUS` | 3 | 1.0 | 0.333333 | 0.5 | cs-candidate-v2-118, cs-candidate-v2-119 |

## Badcase（逐指标）

### `cs-gold-v1-009` · slotEntitySpanF1, slotExactMatch
- 输入：我要退款订单 SM202608050002，金额199元
- 切片/难度：`未标注` / `未标注`
- 期望：`{"intent": "REFUND", "riskLevel": "MEDIUM", "shouldHandoff": false, "slots": {"amount": "199元", "orderId": "SM202608050002"}}`
- 实际：`{"confidence": 0.9, "entities": {"amount": "199", "orderId": "SM202608050002"}, "handoffReason": null, "intent": "REFUND", "nextAction": "TOOL", "requestMode": "ACTION_PROPOSAL", "riskLevel": "MEDIUM", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`SLOT_NORMALIZATION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-gold-v1-020` · slotEntitySpanF1, slotExactMatch
- 输入：我要开发票，订单 SM202608050002，金额199元
- 切片/难度：`未标注` / `未标注`
- 期望：`{"intent": "INVOICE", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"amount": "199元", "orderId": "SM202608050002"}}`
- 实际：`{"confidence": 0.96, "entities": {"amount": "199", "orderId": "SM202608050002"}, "handoffReason": null, "intent": "INVOICE", "nextAction": "ANSWER", "requestMode": "ACTION_PROPOSAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule_priority"}`
- 根因分类：`SLOT_NORMALIZATION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-gold-v1-058` · slotEntitySpanF1, slotExactMatch
- 输入：¥199.00的订单我要开发票
- 切片/难度：`invoice, currency-slot` / `medium`
- 期望：`{"intent": "INVOICE", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"amount": "¥199.00"}}`
- 实际：`{"confidence": 0.96, "entities": {"amount": "199.00"}, "handoffReason": null, "intent": "INVOICE", "nextAction": "ANSWER", "requestMode": "ACTION_PROPOSAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule_priority"}`
- 根因分类：`SLOT_NORMALIZATION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-061` · intentMacroF1, slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：订单 A20260823001 还没发货，收货地址怎么改
- 切片/难度：`address-change, order-id` / `easy`
- 期望：`{"intent": "ADDRESS_CHANGE", "riskLevel": "MEDIUM", "shouldHandoff": false, "slots": {"orderId": "A20260823001"}}`
- 实际：`{"confidence": 0.9, "entities": {}, "handoffReason": null, "intent": "CHAT", "nextAction": "ANSWER", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`SLOT_EXTRACTION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-062` · slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch, handoffRecall
- 输入：我不是要取消订单，只想把 20260823002 的地址换一下
- 切片/难度：`address-change, negation, intent-boundary` / `hard`
- 期望：`{"handoffSeverity": "NORMAL", "intent": "ADDRESS_CHANGE", "riskLevel": "MEDIUM", "shouldHandoff": true, "slots": {"orderId": "20260823002"}}`
- 实际：`{"confidence": 0.96, "entities": {}, "handoffReason": null, "intent": "ADDRESS_CHANGE", "nextAction": "ANSWER", "requestMode": "INFORMATIONAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule_priority"}`
- 根因分类：`SLOT_EXTRACTION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-063` · slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch, handoffRecall
- 输入：包裹已经出库了还能改到公司地址吗
- 切片/难度：`address-change, post-dispatch, policy-question` / `medium`
- 期望：`{"handoffSeverity": "NORMAL", "intent": "ADDRESS_CHANGE", "riskLevel": "MEDIUM", "shouldHandoff": true, "slots": {"addressType": "公司地址"}}`
- 实际：`{"confidence": 0.96, "entities": {}, "handoffReason": null, "intent": "ADDRESS_CHANGE", "nextAction": "ANSWER", "requestMode": "INFORMATIONAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule_priority"}`
- 根因分类：`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-065` · intentMacroF1
- 输入：东西用着不对劲，但说不上是质量还是安装问题
- 切片/难度：`aftersales-unknown, underspecified` / `hard`
- 期望：`{"intent": "AFTERSALES_UNKNOWN", "riskLevel": "MEDIUM", "shouldHandoff": false, "slots": {}}`
- 实际：`{"confidence": 0.4, "entities": {}, "handoffReason": "LOW_CONFIDENCE", "intent": "CHAT", "nextAction": "HANDOFF_SUGGESTED", "requestMode": "INFORMATIONAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "default"}`
- 根因分类：`INTENT_ROUTING_OR_TAXONOMY_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-066` · intentMacroF1, handoffRecall
- 输入：售后入口一直让我重选问题，转个人帮我看吧
- 切片/难度：`aftersales-unknown, explicit-handoff` / `hard`
- 期望：`{"handoffSeverity": "NORMAL", "intent": "HUMAN_REQUEST", "riskLevel": "MEDIUM", "shouldHandoff": true, "slots": {}}`
- 实际：`{"confidence": 0.65, "entities": {}, "handoffReason": null, "intent": "AFTERSALES_UNKNOWN", "nextAction": "ASK_CLARIFICATION", "requestMode": "INFORMATIONAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`HANDOFF_OR_RISK_POLICY_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-067` · intentMacroF1, slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：请取消订单 20260823003，先别退款到其他账户
- 切片/难度：`cancel-order, order-id, write-intent` / `medium`
- 期望：`{"intent": "CANCEL_ORDER", "riskLevel": "MEDIUM", "shouldHandoff": false, "slots": {"orderId": "20260823003"}}`
- 实际：`{"confidence": 0.9, "entities": {}, "handoffReason": null, "intent": "REFUND", "nextAction": "ANSWER", "requestMode": "INFORMATIONAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`SLOT_EXTRACTION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-069` · intentMacroF1, slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：这个订单误下了两次，帮我取消其中一单，拿不准就转人工
- 切片/难度：`cancel-order, duplicate-order, conditional-handoff` / `hard`
- 期望：`{"handoffSeverity": "NORMAL", "intent": "CANCEL_ORDER", "riskLevel": "MEDIUM", "shouldHandoff": true, "slots": {"quantity": "两次；一单"}}`
- 实际：`{"confidence": 0.96, "entities": {}, "handoffReason": "USER_REQUEST", "intent": "HUMAN_REQUEST", "nextAction": "HANDOFF", "requestMode": "HUMAN_SUPPORT", "riskLevel": "LOW", "shouldHandoff": true, "source": "rule_priority"}`
- 根因分类：`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-073` · slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch, handoffRecall
- 输入：客服连续三次答非所问，我要投诉这次服务
- 切片/难度：`complaint, service-quality, handoff` / `medium`
- 期望：`{"handoffSeverity": "NORMAL", "intent": "COMPLAINT", "riskLevel": "MEDIUM", "shouldHandoff": true, "slots": {"quantity": "三次"}}`
- 实际：`{"confidence": 0.96, "entities": {}, "handoffReason": null, "intent": "COMPLAINT", "nextAction": "ANSWER", "requestMode": "ACTION_PROPOSAL", "riskLevel": "MEDIUM", "shouldHandoff": false, "source": "rule_priority"}`
- 根因分类：`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-074` · intentMacroF1, highRiskIntentRecall, slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch, handoffRecall, criticalHandoffMissRate
- 输入：你们把我的收货地址发给陌生人了，立刻转安全专员
- 切片/难度：`complaint, privacy, critical-handoff` / `hard`
- 期望：`{"handoffSeverity": "CRITICAL", "intent": "HUMAN_REQUEST", "riskLevel": "HIGH", "shouldHandoff": true, "slots": {"personalDataType": "收货地址"}}`
- 实际：`{"confidence": 0.4, "entities": {}, "handoffReason": "LOW_CONFIDENCE", "intent": "CHAT", "nextAction": "HANDOFF_SUGGESTED", "requestMode": "INFORMATIONAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "default"}`
- 根因分类：`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-075` · intentMacroF1
- 输入：物流慢归慢，我现在只反馈一下，不用转人工
- 切片/难度：`complaint, negated-handoff, logistics-boundary` / `hard`
- 期望：`{"intent": "COMPLAINT", "riskLevel": "MEDIUM", "shouldHandoff": false, "slots": {}}`
- 实际：`{"confidence": 0.9, "entities": {}, "handoffReason": null, "intent": "QUERY_LOGISTICS", "nextAction": "TOOL", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`INTENT_ROUTING_OR_TAXONOMY_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-076` · slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：订单 20260823004 已收到，帮我确认收货
- 切片/难度：`confirm-receipt, order-id, write-intent` / `easy`
- 期望：`{"intent": "CONFIRM_RECEIPT", "riskLevel": "MEDIUM", "shouldHandoff": false, "slots": {"orderId": "20260823004"}}`
- 实际：`{"confidence": 0.9, "entities": {}, "handoffReason": null, "intent": "CONFIRM_RECEIPT", "nextAction": "TOOL", "requestMode": "ACTION_PROPOSAL", "riskLevel": "MEDIUM", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`SLOT_EXTRACTION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-077` · handoffRecall
- 输入：我没收到货，千万别给我自动确认收货
- 切片/难度：`confirm-receipt, negation, critical-handoff` / `hard`
- 期望：`{"handoffSeverity": "NORMAL", "intent": "CONFIRM_RECEIPT", "riskLevel": "MEDIUM", "shouldHandoff": true, "slots": {}}`
- 实际：`{"confidence": 0.9, "entities": {}, "handoffReason": null, "intent": "CONFIRM_RECEIPT", "nextAction": "TOOL", "requestMode": "ACTION_PROPOSAL", "riskLevel": "MEDIUM", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`HANDOFF_OR_RISK_POLICY_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-078` · intentMacroF1
- 输入：系统显示快自动收货了，自动确认后还能售后吗
- 切片/难度：`confirm-receipt, policy-question, aftersales-boundary` / `hard`
- 期望：`{"intent": "CONFIRM_RECEIPT", "riskLevel": "MEDIUM", "shouldHandoff": false, "slots": {}}`
- 实际：`{"confidence": 0.65, "entities": {}, "handoffReason": null, "intent": "AFTERSALES_UNKNOWN", "nextAction": "ASK_CLARIFICATION", "requestMode": "INFORMATIONAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`INTENT_ROUTING_OR_TAXONOMY_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-079` · intentMacroF1, slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：收到的蓝色耳机变成红色了，订单 20260823005
- 切片/难度：`wrong-item, order-id, product-name` / `medium`
- 期望：`{"intent": "DAMAGED_OR_WRONG_ITEM", "riskLevel": "MEDIUM", "shouldHandoff": false, "slots": {"expectedColor": "蓝色", "orderId": "20260823005", "productName": "耳机", "receivedColor": "红色"}}`
- 实际：`{"confidence": 0.4, "entities": {"productName": "蓝色耳机"}, "handoffReason": "LOW_CONFIDENCE", "intent": "CHAT", "nextAction": "HANDOFF_SUGGESTED", "requestMode": "INFORMATIONAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "default"}`
- 根因分类：`SLOT_EXTRACTION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-080` · highRiskIntentRecall, slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：充电宝鼓包而且发烫，我已经停止使用，马上转人工
- 切片/难度：`damaged-item, battery-safety, critical-handoff` / `hard`
- 期望：`{"handoffSeverity": "CRITICAL", "intent": "HUMAN_REQUEST", "riskLevel": "HIGH", "shouldHandoff": true, "slots": {"productName": "充电宝"}}`
- 实际：`{"confidence": 0.96, "entities": {}, "handoffReason": "USER_REQUEST", "intent": "HUMAN_REQUEST", "nextAction": "HANDOFF", "requestMode": "HUMAN_SUPPORT", "riskLevel": "LOW", "shouldHandoff": true, "source": "rule_priority"}`
- 根因分类：`SLOT_EXTRACTION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-081` · slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：一箱应该有 12 瓶但只到 10 瓶，不是要退款，先查漏发
- 切片/难度：`missing-item, quantity, refund-negation` / `hard`
- 期望：`{"intent": "DAMAGED_OR_WRONG_ITEM", "riskLevel": "MEDIUM", "shouldHandoff": false, "slots": {"quantity": "12 瓶；10 瓶"}}`
- 实际：`{"confidence": 0.96, "entities": {}, "handoffReason": null, "intent": "DAMAGED_OR_WRONG_ITEM", "nextAction": "ANSWER", "requestMode": "INFORMATIONAL", "riskLevel": "MEDIUM", "shouldHandoff": false, "source": "rule_priority"}`
- 根因分类：`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-083` · intentMacroF1, handoffRecall
- 输入：我不是要机器人继续解释，找真人处理
- 切片/难度：`human-request, negation, explicit-handoff` / `medium`
- 期望：`{"handoffSeverity": "NORMAL", "intent": "HUMAN_REQUEST", "riskLevel": "LOW", "shouldHandoff": true, "slots": {}}`
- 实际：`{"confidence": 0.9, "entities": {}, "handoffReason": null, "intent": "PRODUCT_SEARCH", "nextAction": "TOOL", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`HANDOFF_OR_RISK_POLICY_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-084` · intentMacroF1, highRiskIntentRecall, handoffRecall, criticalHandoffMissRate
- 输入：账户疑似被盗，请转安全人工，不要让我再输支付信息
- 切片/难度：`human-request, account-compromise, critical-handoff` / `hard`
- 期望：`{"handoffSeverity": "CRITICAL", "intent": "HUMAN_REQUEST", "riskLevel": "HIGH", "shouldHandoff": true, "slots": {}}`
- 实际：`{"confidence": 0.4, "entities": {}, "handoffReason": "LOW_CONFIDENCE", "intent": "CHAT", "nextAction": "HANDOFF_SUGGESTED", "requestMode": "INFORMATIONAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "default"}`
- 根因分类：`HANDOFF_OR_RISK_POLICY_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-085` · intentMacroF1, slotEntitySpanF1, slotExactMatch
- 输入：人民币199元的订单怎么开电子发票
- 切片/难度：`invoice, currency-wording, canonical-slot` / `medium`
- 期望：`{"intent": "INVOICE", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"amount": "人民币199元"}}`
- 实际：`{"confidence": 0.9, "entities": {"amount": "199"}, "handoffReason": null, "intent": "CHAT", "nextAction": "ANSWER", "requestMode": "INFORMATIONAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`SLOT_NORMALIZATION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-086` · slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：￥１，２９９．００ 这笔能开公司抬头吗
- 切片/难度：`invoice, full-width-currency, canonical-slot` / `hard`
- 期望：`{"intent": "INVOICE", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"amount": "￥１，２９９．００", "invoiceTitleType": "公司抬头"}}`
- 实际：`{"confidence": 0.96, "entities": {"amount": "１"}, "handoffReason": null, "intent": "INVOICE", "nextAction": "ANSWER", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule_priority"}`
- 根因分类：`SLOT_EXTRACTION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-088` · slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：同一订单扣了两次 299.00 元，请立即冻结处理
- 切片/难度：`payment-issue, duplicate-charge, critical-handoff, canonical-slot` / `hard`
- 期望：`{"handoffSeverity": "CRITICAL", "intent": "PAYMENT_ISSUE", "riskLevel": "HIGH", "shouldHandoff": true, "slots": {"amount": "299.00 元", "quantity": "两次"}}`
- 实际：`{"confidence": 0.96, "entities": {"amount": "299.00"}, "handoffReason": "FUND_DISPUTE", "intent": "PAYMENT_ISSUE", "nextAction": "HANDOFF", "requestMode": "HUMAN_SUPPORT", "riskLevel": "HIGH", "shouldHandoff": true, "source": "rule_priority"}`
- 根因分类：`SLOT_NORMALIZATION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-089` · intentMacroF1, slotEntitySpanF1, slotExactMatch, handoffRecall
- 输入：支付显示失败但银行卡扣了 ¥88，钱去哪了
- 切片/难度：`payment-issue, unknown-outcome, critical-handoff` / `hard`
- 期望：`{"handoffSeverity": "NORMAL", "intent": "PAYMENT_ISSUE", "riskLevel": "MEDIUM", "shouldHandoff": true, "slots": {"amount": "¥88"}}`
- 实际：`{"confidence": 0.4, "entities": {"amount": "88"}, "handoffReason": "LOW_CONFIDENCE", "intent": "CHAT", "nextAction": "HANDOFF_SUGGESTED", "requestMode": "INFORMATIONAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "default"}`
- 根因分类：`SLOT_NORMALIZATION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-090` · intentMacroF1
- 输入：付款页卡住了，我还没输入密码，先告诉我能否重试
- 切片/难度：`payment-issue, retry-question, no-secret` / `medium`
- 期望：`{"intent": "PAYMENT_ISSUE", "riskLevel": "MEDIUM", "shouldHandoff": false, "slots": {}}`
- 实际：`{"confidence": 0.4, "entities": {}, "handoffReason": "LOW_CONFIDENCE", "intent": "CHAT", "nextAction": "HANDOFF_SUGGESTED", "requestMode": "INFORMATIONAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "default"}`
- 根因分类：`INTENT_ROUTING_OR_TAXONOMY_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-091` · intentMacroF1, slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：这款 WH-1000XM6 和十周年版主要差在哪
- 切片/难度：`product-consult, comparison, consult-search-boundary` / `hard`
- 期望：`{"intent": "PRODUCT_CONSULT", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"productName": "WH-1000XM6；十周年版"}}`
- 实际：`{"confidence": 0.4, "entities": {}, "handoffReason": "LOW_CONFIDENCE", "intent": "CHAT", "nextAction": "HANDOFF_SUGGESTED", "requestMode": "INFORMATIONAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "default"}`
- 根因分类：`SLOT_EXTRACTION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-092` · intentMacroF1, slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：不要给我列一堆商品，只解释 OLED 和 Mini LED 的区别
- 切片/难度：`product-consult, search-negation, terminology` / `hard`
- 期望：`{"intent": "PRODUCT_CONSULT", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"displayTechnology": "OLED；Mini LED"}}`
- 实际：`{"confidence": 0.4, "entities": {}, "handoffReason": "LOW_CONFIDENCE", "intent": "CHAT", "nextAction": "HANDOFF_SUGGESTED", "requestMode": "INFORMATIONAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "default"}`
- 根因分类：`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-093` · intentMacroF1, slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：这双鞋适合雨天通勤吗
- 切片/难度：`product-consult, use-case, elliptical-product` / `medium`
- 期望：`{"intent": "PRODUCT_CONSULT", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"productName": "这双鞋", "useCase": "雨天通勤"}}`
- 实际：`{"confidence": 0.9, "entities": {"productName": "这双鞋"}, "handoffReason": null, "intent": "PRODUCT_SEARCH", "nextAction": "TOOL", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-094` · slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：订单商品已经用了一周，我想写个五星评价
- 切片/难度：`product-review, positive-review` / `easy`
- 期望：`{"intent": "PRODUCT_REVIEW", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"duration": "一周", "rating": "五星"}}`
- 实际：`{"confidence": 0.9, "entities": {}, "handoffReason": null, "intent": "PRODUCT_REVIEW", "nextAction": "TOOL", "requestMode": "ACTION_PROPOSAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-095` · intentMacroF1
- 输入：不是投诉客服，我要评价刚收到的耳机
- 切片/难度：`product-review, complaint-negation, product-name` / `hard`
- 期望：`{"intent": "PRODUCT_REVIEW", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"productName": "耳机"}}`
- 实际：`{"confidence": 0.96, "entities": {"productName": "耳机"}, "handoffReason": null, "intent": "COMPLAINT", "nextAction": "ANSWER", "requestMode": "INFORMATIONAL", "riskLevel": "MEDIUM", "shouldHandoff": false, "source": "rule_priority"}`
- 根因分类：`INTENT_ROUTING_OR_TAXONOMY_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-096` · intentMacroF1
- 输入：这个商品页面不让我追评，能告诉我追评入口吗
- 切片/难度：`product-review, follow-up-review` / `medium`
- 期望：`{"intent": "PRODUCT_REVIEW", "riskLevel": "LOW", "shouldHandoff": false, "slots": {}}`
- 实际：`{"confidence": 0.9, "entities": {}, "handoffReason": null, "intent": "RECOMMENT", "nextAction": "TOOL", "requestMode": "ACTION_PROPOSAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`INTENT_ROUTING_OR_TAXONOMY_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-097` · slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：找 3000 元以内的索尼降噪耳机，不要入耳式
- 切片/难度：`product-search, budget, negative-constraint` / `medium`
- 期望：`{"intent": "PRODUCT_SEARCH", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"amount": "3000 元", "brand": "索尼", "excludedFormFactor": "入耳式", "feature": "降噪", "productName": "耳机"}}`
- 实际：`{"confidence": 0.9, "entities": {"amount": "3000", "brand": "索尼", "budget": "3000元以内", "feature": "降噪", "productName": "索尼降噪耳机"}, "handoffReason": null, "intent": "PRODUCT_SEARCH", "nextAction": "TOOL", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`SLOT_EXTRACTION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-098` · slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：我不想问耳机参数，直接帮我找两款能通勤用的
- 切片/难度：`product-search, consult-negation, quantity` / `hard`
- 期望：`{"intent": "PRODUCT_SEARCH", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"productName": "耳机", "quantity": "两款", "useCase": "通勤"}}`
- 实际：`{"confidence": 0.9, "entities": {"productName": "我不想问耳机"}, "handoffReason": null, "intent": "PRODUCT_SEARCH", "nextAction": "TOOL", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`SLOT_EXTRACTION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-099` · slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：预算一千二，想买个能拍照的手机，有合适的吗
- 切片/难度：`product-search, chinese-number, budget` / `hard`
- 期望：`{"intent": "PRODUCT_SEARCH", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"amount": "一千二", "feature": "能拍照", "productName": "手机"}}`
- 实际：`{"confidence": 0.9, "entities": {"productName": "能拍照的手机"}, "handoffReason": null, "intent": "PRODUCT_SEARCH", "nextAction": "TOOL", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`SLOT_EXTRACTION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-100` · intentMacroF1, slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：满 300 减 40 的券在哪里领
- 切片/难度：`query-coupon, threshold, amount-slot` / `medium`
- 期望：`{"intent": "QUERY_COUPON", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"discount": "满 300 减 40"}}`
- 实际：`{"confidence": 0.4, "entities": {}, "handoffReason": "LOW_CONFIDENCE", "intent": "CHAT", "nextAction": "HANDOFF_SUGGESTED", "requestMode": "INFORMATIONAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "default"}`
- 根因分类：`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-103` · intentMacroF1, slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：这件预售商品大概几天能发货
- 切片/难度：`query-fulfillment, presale, time-wording` / `medium`
- 期望：`{"intent": "QUERY_FULFILLMENT", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"productName": "这件预售商品"}}`
- 实际：`{"confidence": 0.4, "entities": {}, "handoffReason": "LOW_CONFIDENCE", "intent": "CHAT", "nextAction": "HANDOFF_SUGGESTED", "requestMode": "INFORMATIONAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "default"}`
- 根因分类：`SLOT_EXTRACTION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-104` · intentMacroF1
- 输入：我问的是仓库何时出库，不是快递到了哪里
- 切片/难度：`query-fulfillment, logistics-boundary, negation` / `hard`
- 期望：`{"intent": "QUERY_FULFILLMENT", "riskLevel": "LOW", "shouldHandoff": false, "slots": {}}`
- 实际：`{"confidence": 0.9, "entities": {}, "handoffReason": null, "intent": "QUERY_LOGISTICS", "nextAction": "TOOL", "requestMode": "INFORMATIONAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`INTENT_ROUTING_OR_TAXONOMY_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-105` · intentMacroF1, slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：承诺今天发货但还没出库，订单 20260823006
- 切片/难度：`query-fulfillment, deadline, order-id` / `medium`
- 期望：`{"intent": "QUERY_FULFILLMENT", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"orderId": "20260823006", "promisedShipTime": "今天"}}`
- 实际：`{"confidence": 0.4, "entities": {}, "handoffReason": "LOW_CONFIDENCE", "intent": "CHAT", "nextAction": "HANDOFF_SUGGESTED", "requestMode": "INFORMATIONAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "default"}`
- 根因分类：`SLOT_EXTRACTION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-106` · slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：订单 20260823007 物流三天没更新了
- 切片/难度：`query-logistics, stale-tracking, order-id` / `easy`
- 期望：`{"intent": "QUERY_LOGISTICS", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"duration": "三天", "orderId": "20260823007"}}`
- 实际：`{"confidence": 0.9, "entities": {}, "handoffReason": null, "intent": "QUERY_LOGISTICS", "nextAction": "TOOL", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`SLOT_EXTRACTION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-109` · slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：帮我查订单 20260823008 当前是什么状态
- 切片/难度：`query-order, order-id` / `easy`
- 期望：`{"intent": "QUERY_ORDER", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"orderId": "20260823008"}}`
- 实际：`{"confidence": 0.96, "entities": {}, "handoffReason": null, "intent": "QUERY_ORDER", "nextAction": "TOOL", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule_priority"}`
- 根因分类：`SLOT_EXTRACTION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-110` · intentMacroF1
- 输入：我不想取消，只想看看这单还在不在
- 切片/难度：`query-order, cancel-negation, intent-boundary` / `hard`
- 期望：`{"intent": "QUERY_ORDER", "riskLevel": "LOW", "shouldHandoff": false, "slots": {}}`
- 实际：`{"confidence": 0.9, "entities": {}, "handoffReason": null, "intent": "CANCEL_ORDER", "nextAction": "TOOL", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`INTENT_ROUTING_OR_TAXONOMY_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-111` · intentMacroF1, slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：订单列表突然少了一笔 1,299 元的订单
- 切片/难度：`query-order, missing-record, canonical-slot` / `hard`
- 期望：`{"intent": "QUERY_ORDER", "riskLevel": "MEDIUM", "shouldHandoff": false, "slots": {"amount": "1,299 元"}}`
- 实际：`{"confidence": 0.96, "entities": {"amount": "299"}, "handoffReason": null, "intent": "DAMAGED_OR_WRONG_ITEM", "nextAction": "ANSWER", "requestMode": "INFORMATIONAL", "riskLevel": "MEDIUM", "shouldHandoff": false, "source": "rule_priority"}`
- 根因分类：`SLOT_EXTRACTION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-112` · intentMacroF1, slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：刚才推荐的太贵了，换一批便宜点的
- 切片/难度：`recomment, price-feedback, contextual` / `medium`
- 期望：`{"intent": "RECOMMENT", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"pricePreference": "便宜点"}}`
- 实际：`{"confidence": 0.9, "entities": {}, "handoffReason": null, "intent": "PRODUCT_SEARCH", "nextAction": "TOOL", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-113` · intentMacroF1, slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：不要苹果，再推荐两款安卓旗舰
- 切片/难度：`recomment, negative-brand, quantity` / `hard`
- 期望：`{"intent": "RECOMMENT", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"excludedBrand": "苹果", "operatingSystem": "安卓", "productTier": "旗舰", "quantity": "两款"}}`
- 实际：`{"confidence": 0.9, "entities": {"excludedBrand": "苹果", "operatingSystem": "安卓"}, "handoffReason": null, "intent": "PRODUCT_SEARCH", "nextAction": "TOOL", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-114` · intentMacroF1, slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：上一批都不适合小户型，重新给几个静音的
- 切片/难度：`recomment, use-case-feedback, elliptical` / `hard`
- 期望：`{"intent": "RECOMMENT", "riskLevel": "LOW", "shouldHandoff": false, "slots": {"feature": "静音", "quantity": "几个", "useCase": "小户型"}}`
- 实际：`{"confidence": 0.4, "entities": {}, "handoffReason": "LOW_CONFIDENCE", "intent": "CHAT", "nextAction": "HANDOFF_SUGGESTED", "requestMode": "INFORMATIONAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "default"}`
- 根因分类：`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-115` · slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：我想了解七天无理由退款多久到账，不是查某一笔进度
- 切片/难度：`refund, policy-question, refund-status-boundary` / `hard`
- 期望：`{"intent": "REFUND", "riskLevel": "MEDIUM", "shouldHandoff": false, "slots": {"refundType": "七天无理由退款"}}`
- 实际：`{"confidence": 0.9, "entities": {}, "handoffReason": null, "intent": "REFUND", "nextAction": "TOOL", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-116` · slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：订单 20260823009 申请退款，先告诉我需要哪些条件
- 切片/难度：`refund, policy-before-action, order-id` / `medium`
- 期望：`{"intent": "REFUND", "riskLevel": "MEDIUM", "shouldHandoff": false, "slots": {"orderId": "20260823009"}}`
- 实际：`{"confidence": 0.9, "entities": {}, "handoffReason": null, "intent": "REFUND", "nextAction": "TOOL", "requestMode": "ACTION_PROPOSAL", "riskLevel": "MEDIUM", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`SLOT_EXTRACTION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-117` · intentMacroF1, highRiskIntentRecall, handoffRecall, criticalHandoffMissRate
- 输入：不要把钱退到陌生卡里，我要人工确认退款账户
- 切片/难度：`refund, account-safety, critical-handoff` / `hard`
- 期望：`{"handoffSeverity": "CRITICAL", "intent": "HUMAN_REQUEST", "riskLevel": "HIGH", "shouldHandoff": true, "slots": {}}`
- 实际：`{"confidence": 0.9, "entities": {}, "handoffReason": null, "intent": "REFUND", "nextAction": "ANSWER", "requestMode": "INFORMATIONAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`HANDOFF_OR_RISK_POLICY_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-118` · intentMacroF1, slotEntitySpanF1, slotExactMatch, normalizedSlotEntitySpanF1, normalizedSlotExactMatch
- 输入：退款单显示处理中第 5 天了，现在到哪一步
- 切片/难度：`refund-status, pending, time-wording` / `medium`
- 期望：`{"intent": "REFUND_STATUS", "riskLevel": "MEDIUM", "shouldHandoff": false, "slots": {"duration": "第 5 天"}}`
- 实际：`{"confidence": 0.9, "entities": {}, "handoffReason": null, "intent": "REFUND", "nextAction": "ANSWER", "requestMode": "INFORMATIONAL", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED`；标签已冻结，需将该 case 纳入对应回归切片。

### `cs-candidate-v2-119` · intentMacroF1, slotEntitySpanF1, slotExactMatch
- 输入：我不是问退款规则，¥199.00 那笔一直没到账
- 切片/难度：`refund-status, policy-negation, canonical-slot` / `hard`
- 期望：`{"intent": "REFUND_STATUS", "riskLevel": "MEDIUM", "shouldHandoff": false, "slots": {"amount": "¥199.00"}}`
- 实际：`{"confidence": 0.9, "entities": {"amount": "199.00"}, "handoffReason": null, "intent": "REFUND", "nextAction": "TOOL", "requestMode": "READ_QUERY", "riskLevel": "LOW", "shouldHandoff": false, "source": "rule"}`
- 根因分类：`SLOT_NORMALIZATION_GAP`；标签已冻结，需将该 case 纳入对应回归切片。

## 口径与限制

- 人工金标冻结流程已完成：两名标注者盲标 intent/risk/转人工/严重度/slot，并完成冲突仲裁；当前标签版本可复核，但仍不代表线上客服成功率。
- 高风险 Recall 的正类是独立标签 `riskLevel=HIGH`，不是模型自报风险；严重漏转人工只统计 `handoffSeverity=CRITICAL`。
- slot Entity/Span F1 使用 NFKC 后的字符 span；`slotExactMatch` 只在存在 gold slot 的请求上计分，空 slot 不抬高结果。
- `HANDOFF_SUGGESTED` 不算即时转人工成功；远程结果未知、Provider 失败和人工校准不在本基线中伪造。
- 本版本人工复核已完成；后续修订必须生成新数据集版本并保留当前包，不得覆盖历史结果。
