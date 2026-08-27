# 客服 HTTP 答案仲裁清单

- 总案件：`60`
- 完全一致：`58`
- 待仲裁：`2`
- 案件级一致率：`0.966667`

这份文件只描述双人分歧，不是模型准确率。仲裁者须独立于 `reviewer-a` 和 `reviewer-b`，只基于冻结的用户问题、最终答案、`sourceRefs` 与标注规则判断。

请编辑单独导出的 `adjudication.answer-review-v27.open.jsonl`，每行只填写：
- `finalLabels`：四项最终标签，字段和枚举必须完整；
- `adjudicator`：稳定的第三人标识，不能是两位原标注者；
- `reason`：一句到数句可复核理由，说明答案/证据/风险边界。

不要改写 `caseId`、问题、答案、引用、两位标注结果、源报告哈希或其他字段。完成后交回该 JSONL；维护者会 fail-closed 校验并生成最终只读证据包。

## cs-gold-v1-001

用户问题：我想买索尼 WH-1000XM6，预算 2000 元

分歧字段：`answerCorrect, citationSupport`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"SUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：权威商品检索在本次预算和品牌约束下未返回结果，回答明确限定为本次检索，未扩大为平台无货。

Reviewer B：
```json
{"answerCorrect":false,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：用户指定了索尼的具体型号，但结果摘要只保留品牌和预算；sourceRefs 仅支持完整原始查询无匹配，不能支持扩大到所有索尼商品。

## cs-gold-v1-043

用户问题：不要苹果，推荐安卓手机

分歧字段：`answerCorrect, citationSupport`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"SUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：权威检索和约束证据显示当前同时满足排除苹果与 Android 条件的结果为零，回答限定为本次检索。

Reviewer B：
```json
{"answerCorrect":false,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：用户要求安卓且排除苹果，但结果摘要只写手机；可见证据仅支持该组合约束无匹配，不能支持更宽泛的手机无结果表述。
