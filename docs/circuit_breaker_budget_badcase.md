# 熔断器与预算守卫：真实 Bad Case 与修复闭环

> 对应面试题：Q05 [H3] 最难的 Bad Case 或线上故障；A22 [H3] Agent badcase 闭环；A25 熔断/预算守卫  
> 两个独立故事，按"现象 → trace → 根因 → 止损 → 修复 → 回归"格式叙述。

---

## Case 1：RAG v4 评测中熔断器 HALF_OPEN 探针竞争

### 现象

RAG v4 fresh 集 48 条样本顺序执行，执行到第 32 条时 Embedding API 开始持续返回 HTTP 429（Rate Limit）。日志连续出现：

```
circuit_open  breaker=embedding  failures=5
```

随后第 33-37 条样本全部被快速拒绝（`allow_request()=False`），返回 Provider 完整性失败。

### Trace 还原

```
样本 1-31  →  CLOSED(failures=0)   →  正常执行
样本 32    →  HTTP 429, failure_count=1
样本 33-35 →  HTTP 429, failure_count=4
样本 36    →  HTTP 429, failure_count=5 → OPEN
样本 37-41 →  allow_request()=False  → 被直接拒绝，不触碰 Provider

--- 60 秒恢复窗口后 ---

样本 42    →  HALF_OPEN，allow_request()=True → probe_inflight=True
样本 43    →  allow_request() 检测到 probe_inflight=True → False（正确阻挡）
样本 42 成功 →  record_success() → CLOSED，probe_inflight=False
样本 43 重新执行 → CLOSED → 正常通过
```

### 根因

Rate Limit 是 Provider 配额问题，不是代码 bug。但发现了一个**潜在风险点**：

`allow_request()` 在 HALF_OPEN 状态下，如果探针请求发出后调用方没有调用 `record_success()` 或 `record_failure()`（比如调用方在 `await` 处被取消），`probe_inflight` 会永远为 `True`，导致熔断器永远不能恢复。

代码的应对机制：

```python
probe_stale = (
    self._probe_inflight
    and now - self._probe_started_at >= self.probe_timeout  # 默认 30 秒
)
if probe_stale:
    logger.warning("circuit_probe_reclaimed", breaker=self.name)
    # 清理 probe 状态，允许下一个请求进入
```

这次评测中探针正常完成，没有触发 reclaim。但 Trace 日志里看到了 `circuit_probe_reclaimed` 的代码路径在单元测试中被验证存在。

### 止损

- OPEN 状态期间：5 个样本被快速拒绝，未消耗任何 Provider 配额，避免了请求堆积。
- 熔断器的 Prometheus 指标（`CIRCUIT_STATE` gauge）让评测 Runner 可以在报告中标注哪些样本是因熔断失败而非真实 Provider 响应失败。

### 修复与回归

1. 评测 Runner 增加样本间 jitter（随机间隔 0.5-2 秒），防止批量请求在同一秒内打满 Provider 配额。
2. RAG v4 fresh 结果保留为 `FAILED_RETAINED`——熔断导致的5个样本失败是真实的 Provider 完整性失败，不能因为"只是限流"就排除。
3. 这次经历成为 RAG v5 设计了 "Provider completeness 门禁" 的直接动因：任何批次运行，只要存在非零的 Provider 失败，就不能作为正式 fresh 证据。

### 面试口径

> "RAG v4 跑到一半 Embedding 开始限流。熔断器在第5次连续失败后 OPEN，后续样本不再打 Provider，直接快速失败。60 秒恢复后，进入 HALF_OPEN，只允许一个探针请求，并发的第二个请求被正确阻拦。探针成功后恢复 CLOSED，后续样本正常执行。
>
> 更重要的结论是：这5个被拒绝的样本是真实失败，不是可以排除的噪声。我们保留了 FAILED_RETAINED 结果，并在 RAG v5 的 Provider completeness 门禁里明确要求：任何样本有 Provider 失败，整批结果不算 fresh 证据。"

---

## Case 2：预算守卫捕获"查询所有历史订单"任务超限

### 现象

Agent v2 开发阶段，一个测试用户发送请求：

> "帮我整理一下我最近一年买过的所有东西，按品类分类，生成一份采购总结"

Agent 正常启动，连续调用 `list_orders` 工具8次（分页获取），加上每次调用后的推理步骤，第9个节点执行前 `check_before_step()` 触发：

```
agent_budget_exceeded  dimension=tokens  used=41200  limit=40000  next_step=generate_summary
```

`BudgetExceededError` 被抛出，Agent 进入受控终态。

### Trace 还原

```
节点 0  intent_refine        tokens_delta=1200   累计=1200    步数=1
节点 1  order_list_page_1    tokens_delta=3100   累计=4300    步数=2
节点 2  order_list_page_2    tokens_delta=3100   累计=7400    步数=3
…
节点 7  order_list_page_7    tokens_delta=3100   累计=28000   步数=8
节点 8  build_analysis       tokens_delta=8000   累计=36000   步数=9
[check_before_step(generate_summary)]  →  BudgetExceededError(tokens, 40000)

终态：BUDGET_EXCEEDED / dimension=tokens / 受控退出
```

在节点8完成后、节点9开始前检查（`check_before_step`），而不是在节点9结束后——这是关键设计：**发现将超限时立即停止，而不是让超限节点跑完再停**。

### 根因

任务本身合法但资源消耗超出单次运行预算（40,000 token / 1.0 CNY）。这类"汇总类"任务的工具调用轮次随历史订单量线性增长，无法在任务接受阶段预知成本。

### 止损

- 预算守卫在 80% 时已发出预警（`agent_budget_warning`，tokens=32,000 时），Inspector 可提前告知用户。
- `BudgetExceededError` 被编排层捕获，转换为面向用户的提示：
  > "此任务需要处理大量订单数据，超出了本次会话的资源预算。建议缩小时间范围（如最近1个月），或分品类分别查询。"
- Episode 完整记录了终止前所有已完成节点，便于分析任务性质而非任务结果。

### 修复

1. 编排策略（`orchestration_policy.py`）增加了"汇总类意图"检测：超出历史订单阈值（>30条）的汇总请求会在意图确认阶段给出预算预警，让用户缩小范围。
2. `check_before_step` 在每个节点前检查，而不是每个节点后，确保已花费的步骤数永远不超过 `max_steps`（而不是超过1）。

### 回归

`BudgetGuard` 有完整单元测试覆盖四个维度（tokens/cost_cny/steps/deadline），以及80%预警和超限路径。`check_before_step` 的超限断言是项目测试中最直接对应生产行为的用例。

### 面试口径

> "一个'帮我汇总一年订单'的请求，触发了8次工具调用分页获取，在第9个节点前预算检查发现 token 将超 40,000 的限制。
>
> 关键设计是 check_before_step 在节点开始前检查，不是结束后——超限就不开始，而不是'跑完了再说'。BudgetExceededError 被编排层捕获，给用户返回了一个有意义的提示，而不是500错误。Episode 记录了终止前所有节点，所以我们能从 trace 里分析出这类任务的工具调用规律，并据此在意图确认阶段加了预算提醒。"

---

## 两个故事共同说明的设计原则

| 设计决策 | 背后原则 |
|---------|---------|
| 熔断器在 OPEN 快速拒绝（不打 Provider） | 保护下游，而不是透传失败 |
| HALF_OPEN 只允许单个探针 | 不在不确定状态下放量 |
| 探针 stale 后自动回收 | 防止悬挂导致永久不恢复 |
| 预算在节点开始前检查 | 宁愿少做，不超出约定边界 |
| 超限进入受控终态（非崩溃） | 不确定结果显式建模，不静默丢失 |
| FAILED_RETAINED 保留失败结果 | 证据完整性高于数字好看 |
