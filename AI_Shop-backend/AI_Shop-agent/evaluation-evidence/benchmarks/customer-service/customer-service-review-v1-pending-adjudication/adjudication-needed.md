# 客服双人标注仲裁清单

- 总案件：`60`
- 完全一致：`35`
- 有分歧：`25`
- 案件级一致率：`0.5833`

只依据用户原话和标注指南仲裁；不要查看 draft expected。每个案件在 `adjudication.template.jsonl` 中填写最终标签、仲裁者和理由。

## cs-gold-v1-001

用户原话：我想买索尼 WH-1000XM6，预算 2000 元

分歧字段：`slots`

Reviewer A：
```json
{
  "handoffSeverity": null,
  "intent": "PRODUCT_SEARCH",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "brand": "索尼",
    "budget": "2000元",
    "productName": "WH-1000XM6"
  }
}
```

Reviewer B：
```json
{
  "handoffSeverity": null,
  "intent": "PRODUCT_SEARCH",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "amount": "2000 元",
    "productName": "索尼 WH-1000XM6"
  }
}
```

仲裁备注：

---

## cs-gold-v1-002

用户原话：我想买华为手机，预算 3000 元

分歧字段：`slots`

Reviewer A：
```json
{
  "handoffSeverity": null,
  "intent": "PRODUCT_SEARCH",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "brand": "华为",
    "budget": "3000元",
    "productCategory": "手机"
  }
}
```

Reviewer B：
```json
{
  "handoffSeverity": null,
  "intent": "PRODUCT_SEARCH",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "amount": "3000 元",
    "productName": "华为手机"
  }
}
```

仲裁备注：

---

## cs-gold-v1-003

用户原话：这款耳机支持蓝牙 5.4 吗

分歧字段：`slots`

Reviewer A：
```json
{
  "handoffSeverity": null,
  "intent": "PRODUCT_CONSULT",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "bluetoothVersion": "5.4",
    "productCategory": "耳机"
  }
}
```

Reviewer B：
```json
{
  "handoffSeverity": null,
  "intent": "PRODUCT_CONSULT",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "bluetoothVersion": "5.4",
    "productName": "耳机"
  }
}
```

仲裁备注：

---

## cs-gold-v1-007

用户原话：订单 SM202608050002 怎么还没发货

分歧字段：`riskLevel`

Reviewer A：
```json
{
  "handoffSeverity": null,
  "intent": "QUERY_FULFILLMENT",
  "riskLevel": "MEDIUM",
  "shouldHandoff": false,
  "slots": {
    "orderId": "SM202608050002"
  }
}
```

Reviewer B：
```json
{
  "handoffSeverity": null,
  "intent": "QUERY_FULFILLMENT",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "orderId": "SM202608050002"
  }
}
```

仲裁备注：

---

## cs-gold-v1-008

用户原话：物流一直不动怎么办，订单 SM202608050002

分歧字段：`riskLevel`

Reviewer A：
```json
{
  "handoffSeverity": null,
  "intent": "QUERY_LOGISTICS",
  "riskLevel": "MEDIUM",
  "shouldHandoff": false,
  "slots": {
    "orderId": "SM202608050002"
  }
}
```

Reviewer B：
```json
{
  "handoffSeverity": null,
  "intent": "QUERY_LOGISTICS",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "orderId": "SM202608050002"
  }
}
```

仲裁备注：

---

## cs-gold-v1-014

用户原话：订单 SM202608050002 我已经收到货了，帮我确认收货

分歧字段：`riskLevel`

Reviewer A：
```json
{
  "handoffSeverity": null,
  "intent": "CONFIRM_RECEIPT",
  "riskLevel": "MEDIUM",
  "shouldHandoff": false,
  "slots": {
    "orderId": "SM202608050002"
  }
}
```

Reviewer B：
```json
{
  "handoffSeverity": null,
  "intent": "CONFIRM_RECEIPT",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "orderId": "SM202608050002"
  }
}
```

仲裁备注：

---

## cs-gold-v1-025

用户原话：支付失败了但是钱扣了

分歧字段：`handoffSeverity`

Reviewer A：
```json
{
  "handoffSeverity": "CRITICAL",
  "intent": "PAYMENT_ISSUE",
  "riskLevel": "HIGH",
  "shouldHandoff": true,
  "slots": {}
}
```

Reviewer B：
```json
{
  "handoffSeverity": "NORMAL",
  "intent": "PAYMENT_ISSUE",
  "riskLevel": "HIGH",
  "shouldHandoff": true,
  "slots": {}
}
```

仲裁备注：

---

## cs-gold-v1-029

用户原话：帮我找 500 元以内、不要户外款的男士外套

分歧字段：`slots`

Reviewer A：
```json
{
  "handoffSeverity": null,
  "intent": "PRODUCT_SEARCH",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "budget": "500元以内",
    "excludedStyle": "户外款",
    "productCategory": "男士外套"
  }
}
```

Reviewer B：
```json
{
  "handoffSeverity": null,
  "intent": "PRODUCT_SEARCH",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "amount": "500 元",
    "excludedProductType": "户外款",
    "productName": "男士外套"
  }
}
```

仲裁备注：

---

## cs-gold-v1-030

用户原话：我要退款，金额 88 元

分歧字段：`slots`

Reviewer A：
```json
{
  "handoffSeverity": null,
  "intent": "REFUND",
  "riskLevel": "MEDIUM",
  "shouldHandoff": false,
  "slots": {
    "amount": "88元"
  }
}
```

Reviewer B：
```json
{
  "handoffSeverity": null,
  "intent": "REFUND",
  "riskLevel": "MEDIUM",
  "shouldHandoff": false,
  "slots": {
    "amount": "88 元"
  }
}
```

仲裁备注：

---

## cs-gold-v1-033

用户原话：这副耳机有没有主动降噪

分歧字段：`slots`

Reviewer A：
```json
{
  "handoffSeverity": null,
  "intent": "PRODUCT_CONSULT",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "feature": "主动降噪",
    "productCategory": "耳机"
  }
}
```

Reviewer B：
```json
{
  "handoffSeverity": null,
  "intent": "PRODUCT_CONSULT",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "productName": "耳机"
  }
}
```

仲裁备注：

---

## cs-gold-v1-034

用户原话：推荐 1000 元以内的降噪耳机

分歧字段：`slots`

Reviewer A：
```json
{
  "handoffSeverity": null,
  "intent": "PRODUCT_SEARCH",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "budget": "1000元以内",
    "feature": "降噪",
    "productCategory": "耳机"
  }
}
```

Reviewer B：
```json
{
  "handoffSeverity": null,
  "intent": "PRODUCT_SEARCH",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "amount": "1000 元",
    "productName": "降噪耳机"
  }
}
```

仲裁备注：

---

## cs-gold-v1-037

用户原话：订单 SM202608050002 重复支付了两次

分歧字段：`handoffSeverity`

Reviewer A：
```json
{
  "handoffSeverity": "CRITICAL",
  "intent": "PAYMENT_ISSUE",
  "riskLevel": "HIGH",
  "shouldHandoff": true,
  "slots": {
    "orderId": "SM202608050002"
  }
}
```

Reviewer B：
```json
{
  "handoffSeverity": "NORMAL",
  "intent": "PAYMENT_ISSUE",
  "riskLevel": "HIGH",
  "shouldHandoff": true,
  "slots": {
    "orderId": "SM202608050002"
  }
}
```

仲裁备注：

---

## cs-gold-v1-039

用户原话：收到的耳机碎了

分歧字段：`slots`

Reviewer A：
```json
{
  "handoffSeverity": null,
  "intent": "DAMAGED_OR_WRONG_ITEM",
  "riskLevel": "MEDIUM",
  "shouldHandoff": false,
  "slots": {
    "productCategory": "耳机"
  }
}
```

Reviewer B：
```json
{
  "handoffSeverity": null,
  "intent": "DAMAGED_OR_WRONG_ITEM",
  "riskLevel": "MEDIUM",
  "shouldHandoff": false,
  "slots": {
    "productName": "耳机"
  }
}
```

仲裁备注：

---

## cs-gold-v1-041

用户原话：这款手机续航怎么样

分歧字段：`slots`

Reviewer A：
```json
{
  "handoffSeverity": null,
  "intent": "PRODUCT_CONSULT",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "feature": "续航",
    "productCategory": "手机"
  }
}
```

Reviewer B：
```json
{
  "handoffSeverity": null,
  "intent": "PRODUCT_CONSULT",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "productName": "手机"
  }
}
```

仲裁备注：

---

## cs-gold-v1-042

用户原话：有没有适合学生的平板，预算2000元

分歧字段：`slots`

Reviewer A：
```json
{
  "handoffSeverity": null,
  "intent": "PRODUCT_SEARCH",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "audience": "学生",
    "budget": "2000元",
    "productCategory": "平板"
  }
}
```

Reviewer B：
```json
{
  "handoffSeverity": null,
  "intent": "PRODUCT_SEARCH",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "amount": "2000元",
    "productName": "平板"
  }
}
```

仲裁备注：

---

## cs-gold-v1-043

用户原话：不要苹果，推荐安卓手机

分歧字段：`slots`

Reviewer A：
```json
{
  "handoffSeverity": null,
  "intent": "PRODUCT_SEARCH",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "excludedBrand": "苹果",
    "operatingSystem": "安卓",
    "productCategory": "手机"
  }
}
```

Reviewer B：
```json
{
  "handoffSeverity": null,
  "intent": "PRODUCT_SEARCH",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "excludedBrand": "苹果",
    "productName": "安卓手机"
  }
}
```

仲裁备注：

---

## cs-gold-v1-044

用户原话：这款耳机和另一款相比哪个好

分歧字段：`slots`

Reviewer A：
```json
{
  "handoffSeverity": null,
  "intent": "PRODUCT_CONSULT",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "productCategory": "耳机"
  }
}
```

Reviewer B：
```json
{
  "handoffSeverity": null,
  "intent": "PRODUCT_CONSULT",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "productName": "耳机"
  }
}
```

仲裁备注：

---

## cs-gold-v1-045

用户原话：耳机有主动降噪嘛

分歧字段：`slots`

Reviewer A：
```json
{
  "handoffSeverity": null,
  "intent": "PRODUCT_CONSULT",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "feature": "主动降噪",
    "productCategory": "耳机"
  }
}
```

Reviewer B：
```json
{
  "handoffSeverity": null,
  "intent": "PRODUCT_CONSULT",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "productName": "耳机"
  }
}
```

仲裁备注：

---

## cs-gold-v1-046

用户原话：银行卡被扣了两次

分歧字段：`handoffSeverity`

Reviewer A：
```json
{
  "handoffSeverity": "CRITICAL",
  "intent": "PAYMENT_ISSUE",
  "riskLevel": "HIGH",
  "shouldHandoff": true,
  "slots": {}
}
```

Reviewer B：
```json
{
  "handoffSeverity": "NORMAL",
  "intent": "PAYMENT_ISSUE",
  "riskLevel": "HIGH",
  "shouldHandoff": true,
  "slots": {}
}
```

仲裁备注：

---

## cs-gold-v1-047

用户原话：付款成功但订单没生成

分歧字段：`handoffSeverity`

Reviewer A：
```json
{
  "handoffSeverity": "CRITICAL",
  "intent": "PAYMENT_ISSUE",
  "riskLevel": "HIGH",
  "shouldHandoff": true,
  "slots": {}
}
```

Reviewer B：
```json
{
  "handoffSeverity": "NORMAL",
  "intent": "PAYMENT_ISSUE",
  "riskLevel": "HIGH",
  "shouldHandoff": true,
  "slots": {}
}
```

仲裁备注：

---

## cs-gold-v1-049

用户原话：支付方式有哪些

分歧字段：`intent`

Reviewer A：
```json
{
  "handoffSeverity": null,
  "intent": "PAYMENT_ISSUE",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {}
}
```

Reviewer B：
```json
{
  "handoffSeverity": null,
  "intent": "CHAT",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {}
}
```

仲裁备注：

---

## cs-gold-v1-055

用户原话：少了一个配件，订单SM202608050002

分歧字段：`slots`

Reviewer A：
```json
{
  "handoffSeverity": null,
  "intent": "DAMAGED_OR_WRONG_ITEM",
  "riskLevel": "MEDIUM",
  "shouldHandoff": false,
  "slots": {
    "missingItem": "一个配件",
    "orderId": "SM202608050002"
  }
}
```

Reviewer B：
```json
{
  "handoffSeverity": null,
  "intent": "DAMAGED_OR_WRONG_ITEM",
  "riskLevel": "MEDIUM",
  "shouldHandoff": false,
  "slots": {
    "orderId": "SM202608050002",
    "productName": "配件",
    "quantity": 1
  }
}
```

仲裁备注：

---

## cs-gold-v1-056

用户原话：商品质量有问题想换货

分歧字段：`intent`

Reviewer A：
```json
{
  "handoffSeverity": null,
  "intent": "DAMAGED_OR_WRONG_ITEM",
  "riskLevel": "MEDIUM",
  "shouldHandoff": false,
  "slots": {}
}
```

Reviewer B：
```json
{
  "handoffSeverity": null,
  "intent": "AFTERSALES_UNKNOWN",
  "riskLevel": "MEDIUM",
  "shouldHandoff": false,
  "slots": {}
}
```

仲裁备注：

---

## cs-gold-v1-057

用户原话：退款多久到账呀

分歧字段：`intent, riskLevel`

Reviewer A：
```json
{
  "handoffSeverity": null,
  "intent": "REFUND",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {}
}
```

Reviewer B：
```json
{
  "handoffSeverity": null,
  "intent": "REFUND_STATUS",
  "riskLevel": "MEDIUM",
  "shouldHandoff": false,
  "slots": {}
}
```

仲裁备注：

---

## cs-gold-v1-059

用户原话：手机壳有没有适配 iPhone 15

分歧字段：`slots`

Reviewer A：
```json
{
  "handoffSeverity": null,
  "intent": "PRODUCT_CONSULT",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "compatibleModel": "iPhone 15",
    "productCategory": "手机壳"
  }
}
```

Reviewer B：
```json
{
  "handoffSeverity": null,
  "intent": "PRODUCT_CONSULT",
  "riskLevel": "LOW",
  "shouldHandoff": false,
  "slots": {
    "compatibleWith": "iPhone 15",
    "productName": "手机壳"
  }
}
```

仲裁备注：

---
