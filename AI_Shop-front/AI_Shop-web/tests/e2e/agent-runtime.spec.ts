import { expect, test } from '@playwright/test';

const user = {
  userId: 'e2e-runtime-user',
  nickName: 'Runtime User',
  email: 'runtime@example.test'
};

const ok = (route: import('@playwright/test').Route, data: unknown = {}) =>
  route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ code: 200, info: 'ok', data })
  });

test('mock browser reconnects after a token rejection and keeps the chat log accessible', async ({
  page
}, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', 'the mock runtime flow runs once on mobile');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route((url) => url.pathname.startsWith('/api/'), async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/account/autoLogin' || path === '/api/account/getUserInfo') {
      await ok(route, user);
      return;
    }
    if (path === '/api/agent/loadHistoryMessage') {
      await ok(route, { list: [], pageNo: 1, pageTotal: 1, totalCount: 0 });
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

  let connections = 0;
  await page.routeWebSocket(/\/ws\/?(?:\?.*)?$/, (socket) => {
    connections += 1;
    if (connections === 1) {
      setTimeout(() => socket.close({ code: 1008, reason: 'invalid token' }), 50);
    }
    socket.onMessage((message) => {
      if (message === 'ping') socket.send('pong');
    });
  });

  await page.goto('/ai-assistant?view=mobile');
  await expect(page.getByRole('log', { name: '智能客服对话' })).toBeVisible();
  await expect(page.getByRole('textbox')).toBeVisible();
  await expect.poll(() => connections, { timeout: 15_000 }).toBeGreaterThan(1);
});

test('mock browser waits for the authoritative cancel result before ending a reply', async ({
  page
}, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', 'the mock runtime flow runs once on mobile');
  await page.setViewportSize({ width: 390, height: 844 });
  let cancelCalls = 0;

  await page.route((url) => url.pathname.startsWith('/api/'), async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/account/autoLogin' || path === '/api/account/getUserInfo') {
      await ok(route, user);
      return;
    }
    if (path === '/api/agent/loadHistoryMessage') {
      await ok(route, { list: [], pageNo: 1, pageTotal: 1, totalCount: 0 });
      return;
    }
    if (path === '/api/agent/sendMessage') {
      await ok(route, {
        messageId: 991,
        userMessage: '测试取消竞态',
        assistantMessage: '',
        status: 1,
        runId: 'run-991',
        requestId: 'req-991',
        episodeId: 'ep-991'
      });
      return;
    }
    if (path === '/api/agent/cancelMessage') {
      cancelCalls += 1;
      await ok(route, {
        success: false,
        changed: false,
        messageId: 991,
        messageStatus: 2,
        terminalState: 'SUCCEEDED'
      });
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

  await page.routeWebSocket(/\/ws\/?(?:\?.*)?$/, (socket) => {
    socket.onMessage((message) => {
      if (message === 'ping') socket.send('pong');
    });
  });

  await page.goto('/ai-assistant?view=mobile');
  const input = page.locator('.agent-chat-textarea');
  await input.focus();
  await input.fill('测试取消竞态');
  await page.getByRole('button', { name: '发送消息' }).click();
  const stop = page.getByRole('button', { name: '停止生成' });
  await expect(stop).toBeVisible();
  await stop.click();
  await expect.poll(() => cancelCalls).toBe(1);
  await expect(stop).toHaveCount(0);
});
