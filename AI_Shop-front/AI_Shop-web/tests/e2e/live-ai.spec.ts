import { execFileSync } from 'node:child_process';
import { expect, test, type APIRequestContext } from '@playwright/test';

const liveEnabled = process.env.AISHOP_LIVE_E2E === 'true';
const paymentEnabled = process.env.AISHOP_LIVE_E2E_PAYMENT === 'true';
const demoUserId = '9000000001';

const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

const redisCli = (...args: string[]) =>
  execFileSync(
    'docker',
    [
      'exec',
      'aishop-redis',
      'sh',
      '-ec',
      'REDISCLI_AUTH="$REDIS_PASSWORD" exec redis-cli --no-auth-warning "$@"',
      'aishop-live-e2e',
      ...args
    ],
    { encoding: 'utf8' }
  );

const unwrap = async (response: Awaited<ReturnType<APIRequestContext['get']>>) => {
  expect(response.ok()).toBe(true);
  const body = await response.json();
  expect(body.code, JSON.stringify(body)).toBe(200);
  return body.data;
};

const loginDemoUser = async (request: APIRequestContext) => {
  const captcha = await unwrap(await request.get('/api/account/checkCode'));
  const key = String(captcha?.checkCodeKey || '');
  expect(key).toMatch(/^[0-9a-f-]{36}$/i);
  const raw = redisCli('--raw', 'GET', `mall:checkcode:${key}`).trim();
  const code = raw.startsWith('"') ? JSON.parse(raw) : raw;
  const login = await request.post('/api/account/login', {
    multipart: {
      email: 'demo@smarlect.local',
      password: 'Demo1234',
      checkCode: code,
      checkCodeKey: key
    }
  });
  await unwrap(login);
};

const resetIntentRepeatCounter = (intent: string) => {
  redisCli('DEL', `mall:agent:intent:repeat:${intent}:${demoUserId}`);
};

const productResult = (row: Record<string, unknown>, excludedProductIds = new Set<string>()) => {
  try {
    const payload = JSON.parse(String(row.assistantMessage || ''));
    if (payload?.type !== 'PRODUCT_SEARCH_RESULT' || !Array.isArray(payload.products)) {
      return null;
    }
    const product = payload.products.find(
      (item: Record<string, unknown>) =>
        item?.productId &&
        item?.productName &&
        item?.requestId &&
        !excludedProductIds.has(String(item.productId))
    );
    return product || null;
  } catch {
    return null;
  }
};

test.describe('explicit local full-stack AI flow', () => {
  test.describe.configure({ mode: 'serial' });
  test.skip(!liveEnabled, 'set AISHOP_LIVE_E2E=true and PLAYWRIGHT_BASE_URL to run');

  test('natural-language refund resolves the unique unshipped headphone item', async ({
    context
  }, testInfo) => {
    testInfo.setTimeout(150_000);
    test.skip(testInfo.project.name !== 'mobile', 'the live flow runs once on the mobile UI');
    await loginDemoUser(context.request);
    resetIntentRepeatCounter('REFUND');

    const query = '没发货的耳机我要退款';
    const sent = await unwrap(await context.request.post('/api/agent/sendMessage', {
      multipart: { message: query, fromProduct: 'false' }
    }));
    const messageId = Number(sent?.messageId || 0);
    expect(messageId).toBeGreaterThan(0);

    let actionCard: Record<string, unknown> | null = null;
    await expect.poll(async () => {
      const history = await unwrap(await context.request.post('/api/agent/loadHistoryMessage', {
        multipart: { pageNo: '1' }
      }));
      const rows = Array.isArray(history?.list) ? history.list : [];
      const row = rows.find(
        (item: Record<string, unknown>) => Number(item.messageId) === messageId
      );
      if (!row || Number(row.status) !== 2) return String(row?.status ?? 'missing');
      try {
        const parsed = JSON.parse(String(row.assistantMessage || ''));
        actionCard = parsed && parsed.type === 'ACTION_CONFIRM' ? parsed : null;
      } catch {
        actionCard = null;
      }
      return actionCard ? 'ACTION_CONFIRM' : String(row.assistantMessage || 'invalid');
    }, {
      timeout: 120_000,
      intervals: [1_000, 2_000, 3_000],
      message: `message ${messageId} must finish with a refund confirmation card`
    }).toBe('ACTION_CONFIRM');

    expect(actionCard).toMatchObject({
      type: 'ACTION_CONFIRM',
      actionType: 'REFUND',
      orderId: 'SM202608050002',
      status: 0,
      items: [
        expect.objectContaining({ orderItemId: 'SMITEM202608050002' })
      ]
    });

    const detail = await unwrap(await context.request.post('/api/order/getMyOrderDetail', {
      form: { orderId: 'SM202608050002' }
    }));
    expect(detail.orderStatus).toBe(1);
    const item = (Array.isArray(detail.orderItemList) ? detail.orderItemList : []).find(
      (row: Record<string, unknown>) => row.orderItemId === 'SMITEM202608050002'
    );
    expect(item).toMatchObject({ orderItemStatus: 1 });
  });

  test('ambiguous aftersales selection is durable and idempotent', async ({
    page,
    context
  }, testInfo) => {
    testInfo.setTimeout(150_000);
    test.skip(testInfo.project.name !== 'mobile', 'the live flow runs once on the mobile UI');
    await loginDemoUser(context.request);
    resetIntentRepeatCounter('DAMAGED_OR_WRONG_ITEM');

    const query = '我收到的东西坏了';
    const sent = await unwrap(await context.request.post('/api/agent/sendMessage', {
      multipart: { message: query, fromProduct: 'false' }
    }));
    const sourceMessageId = Number(sent?.messageId || 0);
    expect(sourceMessageId).toBeGreaterThan(0);

    let selectionCard: Record<string, any> | null = null;
    await expect.poll(async () => {
      const history = await unwrap(await context.request.post('/api/agent/loadHistoryMessage', {
        multipart: { pageNo: '1' }
      }));
      const rows = Array.isArray(history?.list) ? history.list : [];
      const row = rows.find(
        (item: Record<string, unknown>) => Number(item.messageId) === sourceMessageId
      );
      if (!row || Number(row.status) !== 2) return String(row?.status ?? 'missing');
      try {
        const parsed = JSON.parse(String(row.assistantMessage || ''));
        selectionCard = parsed?.type === 'ORDER_SELECTION' ? parsed : null;
      } catch {
        selectionCard = null;
      }
      return selectionCard ? 'ORDER_SELECTION' : String(row.assistantMessage || 'invalid');
    }, {
      timeout: 120_000,
      intervals: [1_000, 2_000, 3_000]
    }).toBe('ORDER_SELECTION');

    const card = selectionCard as Record<string, any>;
    expect(card.candidates.length).toBeGreaterThan(1);
    const first = card.candidates[0] as Record<string, unknown>;
    const second = card.candidates[1] as Record<string, unknown>;

    await page.goto('/ai-assistant?view=mobile');
    const group = page.locator('.msg-group').filter({ has: page.getByText(query, { exact: true }) });
    const selectResponse = page.waitForResponse(
      (response) => response.url().includes('/api/agent/selectOrderCandidate')
        && response.request().method() === 'POST'
    );
    await group.last().locator('.select-button').first().click();
    const selectedBody = await (await selectResponse).json();
    expect(selectedBody.code, JSON.stringify(selectedBody)).toBe(200);
    const selectedMessageId = Number(selectedBody.data?.messageId || 0);
    expect(selectedMessageId).toBeGreaterThan(0);
    await expect(group.last().getByRole('button', { name: '已选择' })).toBeDisabled();

    let supportAction: Record<string, unknown> | null = null;
    await expect.poll(async () => {
      const history = await unwrap(await context.request.post('/api/agent/loadHistoryMessage', {
        multipart: { pageNo: '1' }
      }));
      const rows = Array.isArray(history?.list) ? history.list : [];
      const row = rows.find(
        (item: Record<string, unknown>) => Number(item.messageId) === selectedMessageId
      );
      if (Number(row?.status) !== 2) return 'pending';
      try {
        const parsed = JSON.parse(String(row?.assistantMessage || ''));
        supportAction = parsed?.type === 'ACTION_CONFIRM' ? parsed : null;
      } catch {
        supportAction = null;
      }
      return supportAction ? 'ACTION_CONFIRM' : String(row?.assistantMessage || 'invalid');
    }, {
      timeout: 120_000,
      intervals: [1_000, 2_000, 3_000]
    }).toBe('ACTION_CONFIRM');

    expect(supportAction).toMatchObject({
      type: 'ACTION_CONFIRM',
      actionType: 'CREATE_SUPPORT_CASE',
      orderId: String(first.orderId),
      status: 0
    });
    expect(String(supportAction?.summary || '')).toContain(String(first.productName));

    const repeat = await unwrap(await context.request.post('/api/agent/selectOrderCandidate', {
      multipart: {
        selectionId: String(card.selectionId),
        targetType: String(first.targetType),
        targetId: String(first.targetId)
      }
    }));
    expect(Number(repeat.messageId)).toBe(selectedMessageId);

    const conflictResponse = await context.request.post('/api/agent/selectOrderCandidate', {
      multipart: {
        selectionId: String(card.selectionId),
        targetType: String(second.targetType),
        targetId: String(second.targetId)
      }
    });
    expect(conflictResponse.ok()).toBe(true);
    const conflict = await conflictResponse.json();
    expect(conflict.code).toBe(409);
  });

  test('real recommendation exposure validates a click and reaches checkout', async ({
    page,
    context
  }, testInfo) => {
    testInfo.setTimeout(150_000);
    test.skip(testInfo.project.name !== 'mobile', 'the live flow runs once on the mobile UI');
    await loginDemoUser(context.request);
    const existingCart = await unwrap(await context.request.post('/api/productCart/loadProductCart', {
      form: { pageNo: '1' }
    }));
    const excludedProductIds = new Set(
      (Array.isArray(existingCart?.list) ? existingCart.list : [])
        .map((item: Record<string, unknown>) => String(item.productId || ''))
        .filter(Boolean)
    );

    const query = '推荐适合地铁通勤的主动降噪耳机';
    const sent = await unwrap(await context.request.post('/api/agent/sendMessage', {
      multipart: { message: query, fromProduct: 'false' }
    }));
    const messageId = Number(sent?.messageId || 0);
    expect(messageId).toBeGreaterThan(0);

    let product: Record<string, unknown> | null = null;
    await expect.poll(async () => {
      const history = await unwrap(await context.request.post('/api/agent/loadHistoryMessage', {
        multipart: { pageNo: '1' }
      }));
      const rows = Array.isArray(history?.list) ? history.list : [];
      const row = rows.find(
        (item: Record<string, unknown>) => Number(item.messageId) === messageId
      );
      product = row ? productResult(row, excludedProductIds) : null;
      return product ? 'ready' : String(row?.status ?? 'missing');
    }, {
      timeout: 120_000,
      intervals: [1_000, 2_000, 3_000],
      message: `message ${messageId} must finish with a real attributed product result`
    }).toBe('ready');

    const attributedProduct = product as Record<string, unknown>;
    await page.goto('/ai-assistant?view=mobile');
    const productName = String(attributedProduct.productName);
    const productId = String(attributedProduct.productId);
    const requestId = String(attributedProduct.requestId);
    const messageGroup = page.locator('.msg-group').filter({
      has: page.getByText(query, { exact: true })
    });
    const clickResponse = page.waitForResponse(
      (response) => response.url().includes('/api/agent/reportClick') && response.request().method() === 'POST'
    );
    await messageGroup.last()
      .getByRole('button', { name: new RegExp(escapeRegExp(productName)) })
      .first()
      .click();
    const clickBody = await (await clickResponse).json();
    expect(clickBody, JSON.stringify(clickBody)).toMatchObject({
      code: 200,
      data: { productId, requestId }
    });
    await expect(page).toHaveURL(new RegExp(`/product/${productId}$`));

    await page.reload();
    await page.getByRole('button', { name: '立即购买' }).click();
    await expect(page).toHaveURL(/\/checkout$/);
    const item = await page.evaluate(() => {
      const raw = sessionStorage.getItem('eshop_checkout_items');
      return raw ? JSON.parse(raw)[0] : null;
    });
    expect(item).toMatchObject({
      productId,
      aiRequestId: requestId,
      aiPosition: Number(clickBody.data.position),
      aiSource: clickBody.data.source,
      aiAttributedAt: clickBody.data.occurredAt
    });

    const propertyValueIds = String(item?.propertyValueIds || '');
    const position = Number(clickBody.data.position);
    expect(propertyValueIds).not.toBe('');

    await unwrap(await context.request.post('/api/productCart/add2Cart', {
      form: {
        productId,
        propertyValueIds,
        buyCount: '1',
        aiRequestId: requestId,
        aiPosition: String(position)
      }
    }));
    const cart = await unwrap(await context.request.post('/api/productCart/loadProductCart', {
      form: { pageNo: '1' }
    }));
    const cartItem = (Array.isArray(cart?.list) ? cart.list : []).find(
      (row: Record<string, unknown>) =>
        String(row.productId) === productId && String(row.propertyValueIds) === propertyValueIds
    );
    expect(cartItem).toMatchObject({
      productId,
      aiRequestId: requestId,
      aiPosition: position,
      aiSource: clickBody.data.source
    });
    await unwrap(await context.request.post('/api/productCart/deleteCart', {
      form: { cartId: String(cartItem.cartId) }
    }));

    // Payment credentials are intentionally external to the local AI evidence
    // boundary. Checkout and recommendation attribution are the required flow;
    // opt in to the real payment adapter only when its sandbox is configured.
    if (!paymentEnabled) return;

    const addresses = await unwrap(await context.request.get('/api/userAddress/loadDataList'));
    const addressId = String((Array.isArray(addresses) ? addresses[0]?.addressId : '') || '');
    expect(addressId).not.toBe('');
    const payInfo = await unwrap(await context.request.post('/api/order/postOrder', {
      headers: { 'Idempotency-Key': `live-ai-attribution-${requestId}` },
      data: {
        payMethod: 'alipay_wap',
        addressId,
        orderFrom: 0,
        orderList: [{
          productId,
          propertyValueIds,
          buyCount: 1,
          remark: 'AI recommendation attribution E2E',
          aiRequestId: requestId,
          aiPosition: position
        }]
      }
    }));
    const payOrderId = String(payInfo?.payOrderId || '');
    expect(payOrderId).not.toBe('');
    const order = await unwrap(await context.request.post('/api/order/getOrderInfo', {
      form: { payOrderId }
    }));
    const orderId = String(order?.orderId || '');
    expect(orderId).not.toBe('');
    const detail = await unwrap(await context.request.post('/api/order/getMyOrderDetail', {
      form: { orderId }
    }));
    const orderItem = (Array.isArray(detail?.orderItemList) ? detail.orderItemList : []).find(
      (row: Record<string, unknown>) => String(row.productId) === productId
    );
    expect(orderItem).toMatchObject({
      productId,
      aiRequestId: requestId,
      aiPosition: position,
      aiSource: clickBody.data.source
    });

    await unwrap(await context.request.post('/api/order/cancelOrder', {
      form: { orderId }
    }));
    const cancelled = await unwrap(await context.request.post('/api/order/getMyOrderDetail', {
      form: { orderId }
    }));
    expect(cancelled.orderItemList[0]).toMatchObject({ aiRequestId: requestId });
    await unwrap(await context.request.post('/api/order/deleteOrder', {
      form: { orderId }
    }));
  });

  test('conversation clear hides history while preserving the shopping profile', async ({
    page,
    context
  }, testInfo) => {
    testInfo.setTimeout(150_000);
    test.skip(testInfo.project.name !== 'mobile', 'the live flow runs once on the mobile UI');
    await loginDemoUser(context.request);

    const profileBefore = await unwrap(
      await context.request.get('/api/agent/shoppingProfile')
    );
    const query = `请简短回复会话清除功能验收${Date.now()}`;
    const sent = await unwrap(await context.request.post('/api/agent/sendMessage', {
      multipart: { message: query, fromProduct: 'false' }
    }));
    const messageId = Number(sent?.messageId || 0);
    expect(messageId).toBeGreaterThan(0);

    await expect.poll(async () => {
      const history = await unwrap(await context.request.post('/api/agent/loadHistoryMessage', {
        multipart: { pageNo: '1' }
      }));
      const rows = Array.isArray(history?.list) ? history.list : [];
      const row = rows.find(
        (item: Record<string, unknown>) => Number(item.messageId) === messageId
      );
      return Number(row?.status ?? -1);
    }, {
      timeout: 120_000,
      intervals: [1_000, 2_000, 3_000]
    }).toBe(2);

    await page.goto('/ai-assistant?view=mobile');
    const queryText = page.locator('.bubble-row.user .text').filter({ hasText: query });
    await expect(queryText).toBeVisible();

    const clearResponse = page.waitForResponse(
      (response) => response.url().includes('/api/agent/clearHistoryMessage')
        && response.request().method() === 'POST'
    );
    await page.getByRole('button', { name: '清除会话记录' }).click();
    await page.getByRole('button', { name: '确认清除' }).click();
    const clearBody = await (await clearResponse).json();
    expect(clearBody, JSON.stringify(clearBody)).toMatchObject({
      code: 200,
      data: { memoryPreserved: true }
    });
    expect(Number(clearBody.data.clearedThroughMessageId)).toBeGreaterThanOrEqual(messageId);

    await expect(queryText).toHaveCount(0);
    await expect(page.getByText('您好，我是智选智能客服小智')).toBeVisible();

    const historyAfter = await unwrap(
      await context.request.post('/api/agent/loadHistoryMessage', {
        multipart: { pageNo: '1' }
      })
    );
    expect(historyAfter?.list || []).toEqual([]);
    expect(Number(historyAfter?.totalCount || 0)).toBe(0);

    const profileAfter = await unwrap(
      await context.request.get('/api/agent/shoppingProfile')
    );
    expect(profileAfter).toEqual(profileBefore);
  });
});
