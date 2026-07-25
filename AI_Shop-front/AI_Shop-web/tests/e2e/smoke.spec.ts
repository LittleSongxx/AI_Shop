import { expect, test } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.route((url) => url.pathname.startsWith('/api/'), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ code: 200, data: {} })
    });
  });
});

test('login screen renders on the configured viewport', async ({ page }) => {
  await page.goto('/login');
  await expect(page.getByText('欢迎回来')).toBeVisible();
  await expect(page.getByText('简选 · Simlect')).toBeVisible();
  expect(await page.locator('body').evaluate((node) => node.scrollWidth <= window.innerWidth + 1)).toBe(true);
});
