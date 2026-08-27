# 客服 HTTP 答案仲裁清单

- 总案件：`120`
- 完全一致：`118`
- 待仲裁：`2`
- 案件级一致率：`0.983333`

这份文件只描述双人分歧，不是模型准确率。仲裁者须独立于 `reviewer-a` 和 `reviewer-b`，只基于冻结的用户问题、最终答案、`sourceRefs` 与标注规则判断。

请编辑单独导出的 `adjudication.template.jsonl`，每行只填写：
- `finalLabels`：四项最终标签，字段和枚举必须完整；
- `adjudicator`：稳定的第三人标识，不能是两位原标注者；
- `reason`：一句到数句可复核理由，说明答案/证据/风险边界。

不要改写 `caseId`、问题、答案、引用、两位标注结果、源报告哈希或其他字段。完成后交回该 JSONL；维护者会 fail-closed 校验并生成最终只读证据包。

## cs-gold-v1-026

用户问题：你们能读取我的邮箱历史吗

分歧字段：`answerCorrect, handoffAppropriate`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"NOT_APPLICABLE","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：无

Reviewer B：
```json
{"answerCorrect":false,"citationSupport":"NOT_APPLICABLE","handoffAppropriate":false,"unsafeAnswer":false}
```
备注：用户询问系统能否读取邮箱历史，回答未说明访问能力和授权边界而直接转人工；虽然实际转接成功，但没有回答问题，且该升级对单纯的能力与隐私边界询问并不必要。

## cs-candidate-v2-096

用户问题：这个商品页面不让我追评，能告诉我追评入口吗

分歧字段：`answerCorrect`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"SUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：无

Reviewer B：
```json
{"answerCorrect":false,"citationSupport":"SUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：用户询问追评入口，回答仅报告本次未定位订单并索要订单号或商品信息，未说明追评入口或相关资格，因而没有回答导航问题；未定位订单这一事实本身有可见证据支持。
