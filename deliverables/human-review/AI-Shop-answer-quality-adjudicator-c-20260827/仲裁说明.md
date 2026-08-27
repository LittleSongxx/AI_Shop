# 客服 HTTP 答案仲裁清单

- 总案件：`120`
- 完全一致：`117`
- 待仲裁：`3`
- 案件级一致率：`0.975`

这份文件只描述双人分歧，不是模型准确率。仲裁者须独立于 `reviewer-a` 和 `reviewer-b`，只基于冻结的用户问题、最终答案、`sourceRefs` 与标注规则判断。

请编辑单独导出的 `deliverables/human-review/AI-Shop-answer-quality-adjudicator-c-20260827/adjudication.open.jsonl`，每行只填写：
- `finalLabels`：四项最终标签，字段和枚举必须完整；
- `adjudicator`：稳定的第三人标识，不能是两位原标注者；
- `reason`：一句到数句可复核理由，说明答案/证据/风险边界。

不要改写 `caseId`、问题、答案、引用、两位标注结果、源报告哈希或其他字段。完成后交回该 JSONL；维护者会 fail-closed 校验并生成最终只读证据包。

## cs-candidate-v2-079

用户问题：收到的蓝色耳机变成红色了，订单 20260823005

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
备注：用户描述的是颜色不符，确认卡却将工单归为“商品破损”，问题类别与诉求不一致。

## cs-candidate-v2-090

用户问题：付款页卡住了，我还没输入密码，先告诉我能否重试

分歧字段：`answerCorrect`

Reviewer A：
```json
{"answerCorrect":false,"citationSupport":"NOT_APPLICABLE","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：用户已明确尚未输入密码并询问能否重试；回答未给出可否重试的条件或结论，仅再次要求核对扣款或报错。

Reviewer B：
```json
{"answerCorrect":true,"citationSupport":"NOT_APPLICABLE","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：无

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
备注：用户询问追评入口及页面不可用，回答仅要求补充订单信息，没有说明入口或可能的追评资格。
