import { afterEach, describe, expect, it, vi } from 'vitest';

import { agentApi } from '@/api/modules';

describe('agent click reporting transport', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('waits for and returns the server-validated touchpoint', async () => {
    const attribution = {
      requestId: 'request-2',
      productId: 'p2',
      position: 2,
      source: 'hybrid',
      occurredAt: '2026-08-06T09:00:00.000'
    };
    const fetchMock = vi.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ code: 200, data: attribution })
      })
    );
    vi.stubGlobal('fetch', fetchMock);

    await expect(agentApi.reportClick('p2', 'request-2', 2)).resolves.toEqual(attribution);

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, options] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit & { body: FormData }
    ];
    expect(url).toBe('/api/agent/reportClick');
    const body = options.body;
    expect(body.get('productId')).toBe('p2');
    expect(body.get('requestId')).toBe('request-2');
    expect(body.get('position')).toBe('2');
    expect(options.keepalive).toBeUndefined();
    expect(options.signal).toBeInstanceOf(AbortSignal);
  });

  it('rejects a mismatched response instead of persisting forged attribution', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            code: 200,
            data: {
              requestId: 'different',
              productId: 'p3',
              position: 3,
              source: 'hybrid',
              occurredAt: '2026-08-06T09:00:00.000'
            }
          })
      })
    );
    vi.stubGlobal('fetch', fetchMock);

    await expect(agentApi.reportClick('p3', 'request-3', 3)).rejects.toThrow(
      'response mismatch'
    );
  });
});
