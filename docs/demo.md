# 本地演示与验收

## 准备环境

```bash
./start.sh --build
AI_Shop-backend/AI_Shop-agent/.venv/bin/python scripts/bootstrap_demo.py
```

演示数据仅用于本地验证，可重复导入。密钥写入本地 `.env`，不得提交。

## 三分钟主演示

### 导购闭环

1. 登录用户端并打开 AI 助手。
2. 输入：`我预算4500元，主要地铁通勤，需要主动降噪耳机，请推荐一款并说明理由。`
3. 展示商品卡、当前价格库存和推荐依据。
4. 打开商品详情并进入结算。
5. 创建待支付订单，通过 DevTools/API 或管理端证据核验推荐归因、权威 SKU 和物流记录。
6. 不执行真实支付，演示结束后取消演示订单。

### 客服闭环

1. 输入：`我收到的东西坏了，想创建售后工单。`
2. 在多候选时选择订单项。
3. 展示确认卡，并证明确认前没有副作用。
4. 确认一次，通过管理端/API 展示唯一 OPEN 工单、action token 和幂等结果。
5. 重复确认，结果保持不变。

## 浏览器验收

Mock E2E：

```bash
cd AI_Shop-front/AI_Shop-web
npm run test:e2e
```

真实本地闭环：

```bash
AISHOP_LIVE_E2E=true \
AISHOP_LIVE_E2E_ORDER=true \
PLAYWRIGHT_BASE_URL=http://127.0.0.1:6001 \
npx playwright test tests/e2e/live-ai.spec.ts --project=mobile --workers=1
```

`AISHOP_LIVE_E2E_PAYMENT=true` 仅校验支付表单能生成；不执行支付、回调或沙箱全链验收，不属于默认门禁。

## 外部 AI 黑盒试用

当前扩展协议是 v3：一个模型系列、两个隔离会话，每会话 21 项任务（12 项 AI + 9 项 Java 电商底座，共 42 个任务尝试）。Java 阶段直接操作可见网站；AI 阶段通过助手消息验证 Agent。v3 将待支付建单放在最后一项，避免 15 分钟订单超时影响状态判定。每个会话先运行 `prepare`，只把该命令输出目录中的 `task-card.md` 交给在全新浏览器上下文启动的外部 AI。不要复用 IDE 里旧会话的任务卡或已登录标签页；AI 只能访问网站。

```bash
AI_Shop-backend/AI_Shop-agent/.venv/bin/python scripts/blackbox_pilot_v3.py prepare --actor-label '<模型系列>' --session 1
# 外部 AI 按卡片顺序完成 21 项网页任务（Java 阶段不要向助手发送消息）
AI_Shop-backend/AI_Shop-agent/.venv/bin/python scripts/blackbox_pilot_v3.py finalize --session-id <prepare输出的ID>
```

完成同一模型系列的两次会话后：

```bash
AI_Shop-backend/AI_Shop-agent/.venv/bin/python scripts/blackbox_pilot_v3.py aggregate
```

原始 v3 会话保存在 ignored `run/blackbox-pilot-v3/`；v2/旧 v1 结果仍保存在各自归档目录，不与 v3 混算。只有脱敏聚合摘要可以进入 `docs/evidence/`。
