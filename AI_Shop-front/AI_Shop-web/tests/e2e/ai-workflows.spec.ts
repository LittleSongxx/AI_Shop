import { expect, test, type Page, type Route } from '@playwright/test';

const user = {
  userId: 'e2e-user-1',
  nickName: 'E2E User',
  email: 'e2e@example.test'
};

const ok = (route: Route, data: unknown = {}) =>
  route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ code: 200, info: 'ok', data })
  });

const installAuthenticatedApi = async (
  page: Page,
  handler: (route: Route, path: string) => Promise<boolean> | boolean
) => {
  await page.route((url) => url.pathname.startsWith('/api/'), async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (await handler(route, path)) return;
    if (path === '/api/account/autoLogin' || path === '/api/account/getUserInfo') {
      await ok(route, user);
      return;
    }
    if (path === '/api/userNotification/countUnread') {
      await ok(route, 0);
      return;
    }
    if (path === '/api/userMember/loadMemberCenter') {
      await ok(route, {});
      return;
    }
    await ok(route);
  });
};

const installWebSocketMock = async (page: Page) => {
  let send: ((payload: Record<string, unknown>) => void) | undefined;
  await page.routeWebSocket(/\/ws\/?(?:\?.*)?$/, (socket) => {
    send = (payload) => socket.send(JSON.stringify(payload));
    socket.onMessage((message) => {
      if (message === 'ping') socket.send('pong');
    });
  });
  return () => send;
};

test('out-of-order WebSocket frames converge on one terminal message with citations', async ({
  page
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  let releaseHistory!: () => void;
  const historyGate = new Promise<void>((resolve) => {
    releaseHistory = resolve;
  });

  await installAuthenticatedApi(page, async (route, path) => {
    if (path !== '/api/agent/loadHistoryMessage') return false;
    await historyGate;
    await ok(route, {
      list: [
        {
          messageId: 701,
          userMessage: 'HTTP 后到的问题',
          assistantMessage: '',
          status: 1
        }
      ],
      pageNo: 1,
      pageTotal: 1,
      totalCount: 1
    });
    return true;
  });
  const getWsSender = await installWebSocketMock(page);

  await page.goto('/ai-assistant?view=mobile');
  await expect(page.getByText('历史消息加载中…')).toBeVisible();
  await expect.poll(() => Boolean(getWsSender())).toBe(true);

  getWsSender()!({
    messageType: 'agent',
    messageId: '701',
    assistantMessage: '最终回答，保留引用',
    outPutType: 1,
    sourceRefs: [
      {
        type: 'knowledge_chunk',
        title: '配送规则',
        heading: '配送说明',
        snippet: '订单生成后如需修改地址，应联系人工客服。',
        version: 3,
        source: '02-orders-delivery-and-returns.md',
        retrieval: 'rerank'
      }
    ]
  });
  await expect(page.getByText('最终回答，保留引用')).toBeVisible();

  releaseHistory();
  await expect(page.getByText('HTTP 后到的问题')).toBeVisible();
  await expect(page.getByText('最终回答，保留引用')).toHaveCount(1);

  getWsSender()!({ messageId: 701, assistantMessage: '迟到分片', outPutType: 0 });
  getWsSender()!({ messageId: '701', assistantMessage: '', outPutType: 1 });
  getWsSender()!({
    messageId: 701,
    assistantMessage: '最终回答，保留引用',
    outPutType: 1
  });
  getWsSender()!({
    messageType: 'support',
    messageId: '702',
    userMessage: '',
    assistantMessage: '人工客服实时回复',
    outPutType: 1
  });

  await expect(page.getByText('迟到分片')).toHaveCount(0);
  await expect(page.getByText('最终回答，保留引用')).toHaveCount(1);
  await expect(page.getByText('人工客服实时回复')).toHaveCount(1);
  await expect(page.locator('.msg-group')).toHaveCount(2);

  await page.getByText('参考来源（1）').click();
  await expect(page.getByText('配送规则 · 配送说明')).toBeVisible();
  await expect(page.getByText('订单生成后如需修改地址，应联系人工客服。')).toBeVisible();
  await expect(page.locator('.source-panel a')).toHaveCount(0);
});

test('validated recommendation attribution survives refresh and reaches checkout', async ({
  page
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const occurredAt = new Date(Date.now() - 1_000).toISOString();
  let clickReported = false;

  await installAuthenticatedApi(page, async (route, path) => {
    if (path === '/api/agent/loadHistoryMessage') {
      await ok(route, {
        list: [
          {
            messageId: 801,
            userMessage: '推荐一款通勤耳机',
            assistantMessage: JSON.stringify({
              type: 'PRODUCT_SEARCH_RESULT',
              intro: '根据通勤降噪需求，优先推荐：',
              products: [
                {
                  productId: 'p-100',
                  productName: 'E2E 降噪耳机',
                  minPrice: 399,
                  requestId: 'req-e2e-100',
                  reason: '通勤降噪'
                }
              ]
            }),
            bizType: 'product_search',
            status: 2
          }
        ],
        pageNo: 1,
        pageTotal: 1,
        totalCount: 1
      });
      return true;
    }
    if (path === '/api/agent/reportClick') {
      const body = route.request().postData() || '';
      expect(body).toContain('p-100');
      expect(body).toContain('req-e2e-100');
      clickReported = true;
      await ok(route, {
        requestId: 'req-e2e-100',
        productId: 'p-100',
        position: 1,
        source: 'agent_search',
        occurredAt
      });
      return true;
    }
    if (path === '/api/product/getProduct') {
      await ok(route, {
        productInfo: {
          productId: 'p-100',
          productName: 'E2E 降噪耳机',
          minPrice: 399,
          status: 1,
          stock: 20,
          productDesc: '用于浏览器归因回归。'
        },
        productPropertyList: [
          {
            propertyId: 'color',
            propertyName: '颜色',
            propertyValues: [{ propertyValueId: 'black', propertyValue: '黑色' }]
          }
        ],
        skuList: [
          {
            propertyValueIds: 'black',
            propertyValueIdHash: 'hash-black',
            price: 399,
            stock: 20
          }
        ]
      });
      return true;
    }
    if (path === '/api/order/comment/loadComment') {
      await ok(route, { list: [], totalCount: 0 });
      return true;
    }
    if (path === '/api/order/comment/getProductCommentStats') {
      await ok(route, { totalCount: 0, goodRatePercent: 100, imageCount: 0 });
      return true;
    }
    if (path === '/api/userFavorite/isFavorite') {
      await ok(route, false);
      return true;
    }
    if (path === '/api/product/loadCommendProduct') {
      await ok(route, []);
      return true;
    }
    return false;
  });
  await installWebSocketMock(page);

  await page.goto('/ai-assistant?view=mobile');
  const product = page.getByRole('button', { name: /E2E 降噪耳机/ });
  await expect(product).toBeVisible();
  await product.click();
  await expect(page).toHaveURL(/\/product\/p-100$/);
  await expect.poll(() => clickReported).toBe(true);
  await expect(page.getByRole('heading', { name: 'E2E 降噪耳机' })).toBeVisible();

  // The detail API may be served from the session cache after reload, while
  // the desktop component can also be selected lazily. Wait for the rendered
  // business fact rather than assuming a network response must occur.
  await page.reload();
  await expect(page.getByRole('heading', { name: 'E2E 降噪耳机' })).toBeVisible({ timeout: 15_000 });
  await page.getByRole('button', { name: '立即购买' }).click();
  await expect(page).toHaveURL(/\/checkout$/);

  const checkoutItem = await page.evaluate(() => {
    const raw = sessionStorage.getItem('eshop_checkout_items');
    return raw ? JSON.parse(raw)[0] : null;
  });
  expect(checkoutItem).toMatchObject({
    productId: 'p-100',
    aiRequestId: 'req-e2e-100',
    aiPosition: 1,
    aiSource: 'agent_search',
    aiAttributedAt: occurredAt
  });
});

test('clearing visible conversation preserves agent memory by contract', async ({
  page
}, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', 'the conversation toolbar is covered on mobile here');
  let clearCalls = 0;

  await installAuthenticatedApi(page, async (route, path) => {
    if (path === '/api/agent/loadHistoryMessage') {
      await ok(route, {
        list: [
          {
            messageId: 901,
            userMessage: '继续按我的预算推荐',
            assistantMessage: '已沿用你的预算偏好。',
            status: 2
          }
        ],
        pageNo: 1,
        pageTotal: 1,
        totalCount: 1
      });
      return true;
    }
    if (path === '/api/agent/clearHistoryMessage') {
      clearCalls += 1;
      await ok(route, { clearedThroughMessageId: 901, memoryPreserved: true });
      return true;
    }
    return false;
  });
  await installWebSocketMock(page);

  await page.goto('/ai-assistant?view=mobile');
  await expect(page.getByText('继续按我的预算推荐')).toBeVisible();

  const clearButton = page.getByRole('button', { name: '清除会话记录' });
  await expect(clearButton).toBeVisible();
  const box = await clearButton.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(390);

  await clearButton.click();
  await expect(page.getByText('购物偏好、长期记忆和待确认操作都会保留')).toBeVisible();
  await page.getByRole('button', { name: '确认清除' }).click();

  await expect.poll(() => clearCalls).toBe(1);
  await expect(page.getByText('继续按我的预算推荐')).toHaveCount(0);
  await expect(page.getByText('您好，我是智选智能客服小智')).toBeVisible();
  await expect(page.getByText('会话记录已清除，已有记忆仍会保留')).toBeVisible();
});
