# AI 辅助开发审查记录

> 对应面试题：A30 [H3] AI Coding 质量保证；Q06 [H3] "AI 写了哪些代码，你能维护吗"  
> 本文记录两个典型 PR 中 AI 生成代码被人工审查发现并修正的问题，以及项目中使用的 Skill/任务拆解规范。

## 一、AI 辅助开发流程

```
1. 任务拆解  →  2. AI 生成初稿  →  3. 人工审查清单  →  4. 修改 + 测试  →  5. 边界用例补全
```

规则：
- 每个 AI 生成的方法/类，必须能独立解释其状态机、失败语义和权限边界。
- 对任何写操作（退款、取消、库存），人工审查不能省略。
- 测试用边界 case 必须人工编写，不接受 AI 生成的 `@Test void happyPath()`。

---

## 二、典型案例 1：移除通用重试装饰器

### AI 生成的初稿

```python
@retry(max_attempts=3, backoff=exponential)
async def call_refund_tool(params: dict) -> dict:
    return await mcp_client.call("refund", params)
```

AI 的出发点是提高工具调用的健壮性，逻辑上没问题——网络抖动时重试是常见做法。

### 人工审查发现的问题

退款是**不幂等写操作**。超时不代表远端失败，可能是：
- 请求已发出，响应丢失
- 服务正在处理中，尚未返回

自动重试会导致**重复退款**。三次重试 = 三次退款请求落库。

### 修改方案

完全移除通用重试装饰器。改为：

1. 写操作用幂等键（Java 端 `OrderRequestIdempotency`）吸收重复
2. 超时进入 `INCONCLUSIVE` 状态，由 Worker 定时对账（`pending_action_service.py`）
3. 对账达到边界次数后转 `MANUAL_REVIEW`，不自动重试

```python
# 不能通用重试的原因已在注释中明确
# 工具调用超时 → INCONCLUSIVE → 对账 → MANUAL_REVIEW，禁止自动重放
result = await mcp_client.call("refund", params)
```

### 面试口径

> "AI 生成了通用重试装饰器，第一反应是'对健壮性有好处'。但审查时意识到退款不是查询——超时不等于失败。自动重试会把一次退款变成三次。我们的解决方案是把不确定结果显式建模成 INCONCLUSIVE 状态，让对账周期而非重试次数来解决问题。这个决定让测试也更清晰：`AgentActionStatusServiceTest` 里有专门的 `processingLedgerWithoutDomainEvidenceBecomesInconclusive` 用例验证这条路径。"

---

## 三、典型案例 2：RefundReviewService 冻结字段校验

### AI 生成的初稿

```java
public RefundRequest approve(String refundRequestId, String reviewId, ...) {
    RefundRequest request = selectByIdForUpdate(refundRequestId);
    validateStatus(request);  // 只检查状态是 MANUAL_REVIEW
    refundRequestMapper.reviewApprove(refundRequestId, resolveOrigin(request));
    return selectById(refundRequestId);
}
```

AI 实现了 CAS 状态翻转和幂等台账，逻辑结构正确。

### 人工审查发现的问题

退款请求从发起到进入 MANUAL_REVIEW，可能经过数小时或数天的人工处理窗口。在这段时间内：
- 商家可以修改商品价格
- 平台可以修改规格属性
- 用户可以退货/换货影响数量

如果用退款申请时冻结的金额去执行，但订单项当前金额已经变化，会**退错金额**。

### 修改方案

在状态校验通过后、CAS 执行前，增加冻结字段重校验：

```java
private void revalidateFrozenFields(RefundRequest request) {
    OrderItem item = orderItemMapper.selectByOrderItemId(request.getOrderItemId());
    // 注意：STOCK_PENDING 阶段资金已退，明细已翻 REFUND(0)，
    // 此时金额/数量校验失去意义，只保留存在性检查
    if (RefundSagaStatus.STOCK_PENDING.name().equals(request.getReviewOriginStatus())) {
        return;
    }
    if (!eqAmount(request.getRefundAmount(), item.getItemAmount())) {
        throw new BusinessException("退款金额与订单项当前金额不一致，请核实后重新审批");
    }
    // ... 数量、属性同理
}
```

同时新增了 3 个专项测试：
- `approveRejectsWhenFrozenAmountDrifted`
- `approveRejectsWhenFrozenQuantityDrifted`
- `approveRejectsWhenPropertyDrifted`

### 面试口径

> "AI 实现了幂等台账和 CAS，这部分质量很好。但漏掉了一个业务场景：退款审批可能发生在申请很久之后，商品价格可能已经变化。人工审查时想到'如果按冻结金额退款，但订单项当前金额不同会怎样'——这会退错钱。加了 revalidateFrozenFields() 之后，还要注意 STOCK_PENDING 阶段资金已出、明细已翻 REFUND，此时校验反而会误拦，需要按阶段分支处理。这类跨阶段状态机的细节是 AI 不容易自动推导出来的地方。"

---

## 四、Skill 规范（任务拆解边界）

项目中使用以下约定对 AI 任务进行边界控制：

| 允许 AI 完成 | 必须人工完成或审查 |
|------------|----------------|
| 数据结构定义、Entity/DTO | 任何写操作的状态机边界 |
| 简单 CRUD 的 Mapper 接口 | 幂等键设计和冲突处理 |
| 单元测试 happy path 框架 | 并发/超时/重试的失败语义 |
| 文档初稿 | 权限检查（归属校验、角色边界） |
| Ruff/ESLint 问题修复 | 安全相关（注入、IDOR、PII）逻辑 |

### 关键验收标准（每个 AI 生成的写操作必须通过）

- [ ] 超时返回不确定结果时，系统是什么状态？
- [ ] 同一操作被重复发送时，第二次会发生什么？
- [ ] 如果下游服务返回错误，幂等台账的状态是什么？
- [ ] 是否有测试用例覆盖以上三种场景？

---

## 五、关于"你能维护吗"的回答骨架

> "AI 生成了大量样板代码，但我对每一个状态机节点和失败路径都有独立的理解。  
> 核心证据是：每个关键边界都有对应的测试用例，而且这些测试是人工编写的——不是 AI 生成的 happy path，而是对应了'超时不重试''金额漂移拒绝''并发幂等竞态'这类具体失败场景。  
> RefundReviewService 有 24 个测试，OrderRequestIdempotencyService 有 19 个，AgentActionStatusService 有 12 个，都覆盖了状态机的每个分支，包括这两个被审查修正的案例。"
