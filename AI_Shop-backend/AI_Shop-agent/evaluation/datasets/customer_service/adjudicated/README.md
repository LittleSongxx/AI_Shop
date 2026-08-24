# 客服人工数据复用

这里保存两套不可混用的数据：

- `gold-v1-human-adjudicated.jsonl`：60 条意图、风险、槽位和转人工金标，可作为新的规则/解析器输入 gold。来源为双人盲标和第三人仲裁，哈希写在同名 manifest 中。
- `answer-review-v2-adjudicated.labels.jsonl`：固定 HTTP 回放 `customer-service-http-v1-20260823` 的 60 条最终答案标签，只能审计这一批完全相同的答案。加载时会校验 source report SHA-256 和每条 answer SHA-256，输出变化后不得复用。

推荐使用：

```bash
conda activate shop
python -m evaluation.customer_service_human_data
```

人工标签是质量证据，不等同于生产准确率。金额单位、退款政策与退款进度、比较请求与商品咨询之间存在标注规范边界，见 `docs/evaluation/customer-service/客服标注审计.md`。
