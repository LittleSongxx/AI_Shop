# 订单引用解析 = Query Rewrite：面试故事

> 对应面试题：A34 [H2] 多轮对话指代消解、Query Rewrite 偏离原意防护  
> 代码入口：[order_reference_resolver.py](../AI_Shop-backend/AI_Shop-agent/app/services/order_reference_resolver.py)（671 行）

## 一、问题

售后场景下，用户不说"订单号 SM20260817001"，而是说：

> "我想退那个蓝色手机壳"  
> "昨天买的东西发货了吗"  
> "上次那个还在路上，我想取消"

直接把这段文字送进数据库查询会完全失效。最简单的做法是反问"请提供订单号"，但这对已购买多次的用户体验极差。

## 二、实现的 Query Rewrite 管道

不使用 LLM 来"改写"——而是用确定性规则把自然语言拆解为结构化过滤器，再在候选集上评分排序。

```
用户自然语言
     │
     ▼
① 显式 ID 提取        "SM20260817001" → explicit_order_id
② 状态词映射          "待发货/在路上" → status_filter={已付款,已发货}
③ 时间线索            "昨天/最近/前几天" → time_window
④ 商品语义词          "蓝色手机壳" → topic_terms=[蓝色, 手机壳]
⑤ 反代词识别          "那个/它/这件" → 触发 consult_card 对齐
     │
     ▼
Java 权威接口拉取候选订单（最多30条，最近90天）
     │
     ▼
按意图过滤可操作状态（退款/确认收货/物流状态 各有不同的合法状态集）
     │
     ▼
依次应用 status_filter → time_window → topic_terms → explicit_terms 精确匹配
     │
     ▼
RESOLVED / AMBIGUOUS / NO_MATCH / NO_ELIGIBLE / DEPENDENCY_ERROR
```

### 改写前后对比

| 用户输入 | 改写后的结构化查询参数 |
|---------|-------------------|
| "退那个蓝色手机壳" | `intent=REFUND, statusFilter=[已付款,已发货,已完成], productTerms=[蓝色,手机壳], timeWindow=近90天` |
| "昨天买的东西" | `timeWindow=[T-1 00:00, T 23:59]` |
| "最近那单还没发货" | `statusFilter=[已付款], timeWindow=近7天, recentHint=true` |
| "SM20260817001 那个" | `explicit_order_id=SM20260817001` → 直接精确查询，跳过时间/商品过滤 |

## 三、输出结果分类

| 结果 | 含义 | 后续动作 |
|------|------|---------|
| `RESOLVED` | 唯一匹配 | 进入业务执行路径 |
| `AMBIGUOUS` | 多个候选 | 持久化候选列表，发出澄清卡，下一轮 pending_reference 继续解析 |
| `NO_MATCH` | 无订单匹配线索 | 提示用户补充信息或提供订单号 |
| `NO_ELIGIBLE` | 有匹配但当前意图不可操作（已退款的订单再申请退款） | 解释具体原因，不暴露不相关订单 |
| `DEPENDENCY_ERROR` | Java 服务不可用 | 降级到人工提示，不崩溃 |

## 四、多轮指代消解

`AMBIGUOUS` 时系统将候选列表作为 `pending_reference` 持久化进 Redis Checkpoint。
下一轮用户说"就是那个贵的"或点选卡片，解析器读取 pending_reference 而不是重新查询所有订单，从已有候选中再次过滤——避免"改写后偏离原意"（A34 追问点）。

```python
pending_valid = self._pending_reference_valid(pending_reference, intent)
pending_item = pending_reference.get("orderItemId") if pending_valid else None
pending_order = pending_reference.get("orderId") if pending_valid else None
```

## 五、防止改写偏离原意

两个关键防护：

1. **非商品词过滤**：`_NON_PRODUCT_CLUE_PHRASES` 收录了"退款/取消/最近/查询"等超过50个高频干扰词，这些词不作为商品语义词送入 topic_terms，否则"帮我取消订单"里的"取消"会被当成商品名去匹配。

2. **显式词优先**：在 topic_terms 命中后，进一步检查词是否出现在原始 user_text 中（`explicit_topic_terms`），对精确出现的词再做一轮精确匹配筛选。用户说"蓝色手机壳"不会命中"红色手机壳"（虽然"手机壳"相同）。

## 六、面试答题骨架对应

| A34 追问 | 本项目的回答 |
|---------|------------|
| 如何处理多轮指代（"那个" "它"）？ | pending_reference 携带上轮候选，下轮继续消解，不丢失上文 |
| 改写如何防止偏离原意？ | 确定性规则而非模型改写；非商品词表过滤；explicit_terms 精确优先 |
| 改写后没有结果怎么办？ | NO_MATCH → 降级回人工引导，不丢用户意图 |
| 有没有评测数据？ | task_success_v1.jsonl 中包含8条 REFUND/CANCEL 任务，全部经过 order_reference_resolver；RESOLVED 路径 6/8，AMBIGUOUS→澄清轮 2/8（符合预期，候选多于一时触发）；无 DEPENDENCY_ERROR |

