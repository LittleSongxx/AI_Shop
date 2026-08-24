# 客服 HTTP 答案仲裁清单

- 总案件：`60`
- 完全一致：`52`
- 待仲裁：`8`
- 案件级一致率：`0.866667`

这份文件只描述双人分歧，不是模型准确率。仲裁者须独立于 `reviewer-a` 和 `reviewer-b`，只基于冻结的用户问题、最终答案、`sourceRefs` 与标注规则判断。

请编辑单独导出的 `adjudication.answer-review-v2.open.jsonl`，每行只填写：
- `finalLabels`：四项最终标签，字段和枚举必须完整；
- `adjudicator`：稳定的第三人标识，不能是两位原标注者；
- `reason`：一句到数句可复核理由，说明答案/证据/风险边界。

不要改写 `caseId`、问题、答案、引用、两位标注结果、源报告哈希或其他字段。完成后交回该 JSONL；维护者会 fail-closed 校验并生成最终只读证据包。

## cs-gold-v1-012

用户问题：我要取消订单 SM202608050002

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
备注：sourceRefs 已明确取消订单的条件，但答案只称引用无效并让用户重试或转人工，遗漏了可直接说明的处理条件。

## cs-gold-v1-027

用户问题：我想申请售后，订单 SM202608050002

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
备注：已给出可说明售后申请流程的 sourceRefs，但答案仅称引用无效并让用户重试或转人工，未处理售后请求。

## cs-gold-v1-030

用户问题：我要退款，金额 88 元

分歧字段：`citationSupport`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：问题类型：引用不支持
依据：答案断言未找到退款目标；列出的 sourceRefs 未提供对应证据。
影响：该事实无法由引用核对。

Reviewer B：
```json
{"answerCorrect":true,"citationSupport":"NOT_APPLICABLE","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：无

## cs-gold-v1-033

用户问题：这副耳机有没有主动降噪

分歧字段：`answerCorrect`

Reviewer A：
```json
{"answerCorrect":false,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：问题类型：答非所问/硬约束；引用不支持
依据：用户询问特定耳机是否支持主动降噪，答案改为泛化商品搜索，未给出该属性结论。 返回的商品、价格和库存事实也没有 sourceRefs 支持。
影响：用户请求未被正确处理，且相关事实无法由引用核对。

Reviewer B：
```json
{"answerCorrect":true,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：答案包含具体耳机的降噪、价格、库存和在售信息，但 sourceRefs 为空，无法支持这些商品事实。

## cs-gold-v1-041

用户问题：这款手机续航怎么样

分歧字段：`answerCorrect`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：问题类型：引用不支持
依据：答案附带两个手机的商品、价格和库存事实，但 sourceRefs 为空；其中澄清“未选择具体手机”的方向本身合理；列出的 sourceRefs 未提供对应证据。
影响：该事实无法由引用核对。

Reviewer B：
```json
{"answerCorrect":false,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：缺少具体手机型号时应先澄清；答案却混入未说明续航表现的候选商品及其价格、库存，未直接处理请求，且 sourceRefs 为空。

## cs-gold-v1-044

用户问题：这款耳机和另一款相比哪个好

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
备注：比较需要先明确两款耳机的名称、型号或链接；答案改问使用场景，未补齐关键对象信息，无法进行比较。

## cs-gold-v1-045

用户问题：耳机有主动降噪嘛

分歧字段：`answerCorrect`

Reviewer A：
```json
{"answerCorrect":false,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：问题类型：答非所问/硬约束；引用不支持
依据：用户询问耳机是否有主动降噪，答案只返回泛化搜索结果，未明确回答该属性。 商品名称、价格、库存和属性事实没有 sourceRefs 支持。
影响：用户请求未被正确处理，且相关事实无法由引用核对。

Reviewer B：
```json
{"answerCorrect":true,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：答案包含具体耳机的降噪、价格、库存和在售信息，但 sourceRefs 为空，无法支持这些商品事实。

## cs-gold-v1-056

用户问题：商品质量有问题想换货

分歧字段：`citationSupport`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：问题类型：引用不支持
依据：引用只支持从订单详情发起售后，未直接支持创建 CREATE_SUPPORT_CASE 工单及将质量问题归类为商品破损；列出的 sourceRefs 未提供对应证据。
影响：该事实无法由引用核对。

Reviewer B：
```json
{"answerCorrect":true,"citationSupport":"SUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：无
