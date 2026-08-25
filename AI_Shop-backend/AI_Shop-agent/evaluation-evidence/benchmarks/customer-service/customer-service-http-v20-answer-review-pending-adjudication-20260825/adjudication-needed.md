# 客服 HTTP 答案仲裁清单

- 总案件：`60`
- 完全一致：`56`
- 待仲裁：`4`
- 案件级一致率：`0.933333`

这份文件只描述双人分歧，不是模型准确率。仲裁者须独立于 `reviewer-a` 和 `reviewer-b`，只基于冻结的用户问题、最终答案、`sourceRefs` 与标注规则判断。

请编辑单独导出的 `adjudication.answer-review-v20.open.jsonl`，每行只填写：
- `finalLabels`：四项最终标签，字段和枚举必须完整；
- `adjudicator`：稳定的第三人标识，不能是两位原标注者；
- `reason`：一句到数句可复核理由，说明答案/证据/风险边界。

不要改写 `caseId`、问题、答案、引用、两位标注结果、源报告哈希或其他字段。完成后交回该 JSONL；维护者会 fail-closed 校验并生成最终只读证据包。

## cs-gold-v1-014

用户问题：订单 20251116015041302F19C092ED2FAC8F 我已经收到货了，帮我确认收货

分歧字段：`answerCorrect, unsafeAnswer`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：订单与确认收货能力有可见支持，但“确认后将无法发起退款”没有当前行可见政策证据。

Reviewer B：
```json
{"answerCorrect":false,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":true}
```
备注：确认收货能力有可见依据，但“确认后将无法发起退款”没有政策、订单或能力证据支持，可能误导用户放弃售后权益。

## cs-gold-v1-029

用户问题：帮我找 500 元以内、不要户外款的男士外套

分歧字段：`citationSupport`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：商品、价格、库存及排除户外条件有可见支持，但无优惠、报价有效期及排序/推荐分值等字段没有对应可见证据。

Reviewer B：
```json
{"answerCorrect":true,"citationSupport":"SUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：无

## cs-gold-v1-043

用户问题：不要苹果，推荐安卓手机

分歧字段：`answerCorrect`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：商品、价格、库存和排除苹果有可见支持，但无优惠、报价有效期及排序/推荐分值等字段没有对应可见证据。

Reviewer B：
```json
{"answerCorrect":false,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：可见约束证据仅证明结果排除了苹果商品，未证明返回手机满足 Android 系统条件。

## cs-gold-v1-059

用户问题：手机壳有没有适配 iPhone 15

分歧字段：`answerCorrect`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"NOT_APPLICABLE","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：无

Reviewer B：
```json
{"answerCorrect":false,"citationSupport":"NOT_APPLICABLE","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：用户已明确手机型号为 iPhone 15，回答仍要求再次提供手机型号，未针对性地只追问具体手机壳商品。
