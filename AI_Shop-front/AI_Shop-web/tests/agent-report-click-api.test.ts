import { afterEach, describe, expect, it, vi } from 'vitest';

import { agentApi } from '@/api/modules';

describe('agent click reporting transport', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('queues the small authenticated form through sendBeacon', async () => {
    const sendBeacon = vi.fn(() => true);
    vi.stubGlobal('navigator', { sendBeacon });

    await agentApi.reportClick('p2', 'request-2', 2);

    expect(sendBeacon).toHaveBeenCalledOnce();
    const [url, body] = sendBeacon.mock.calls[0] as unknown as [string, FormData];
    expect(url).toBe('/api/agent/reportClick');
    expect(body.get('productId')).toBe('p2');
    expect(body.get('requestId')).toBe('request-2');
    expect(body.get('position')).toBe('2');
  });

  it('uses a keepalive request when the beacon queue is full', async () => {
    const sendBeacon = vi.fn(() => false);
    const fetchMock = vi.fn(() => Promise.resolve({ ok: true, status: 200 }));
    vi.stubGlobal('navigator', { sendBeacon });
    vi.stubGlobal('fetch', fetchMock);

    await agentApi.reportClick('p3', 'request-3', 3);

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/agent/reportClick',
      expect.objectContaining({
        method: 'POST',
        credentials: 'include',
        keepalive: true
      })
    );
  });
});
