import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const authStore = { isLoggedIn: true };
const messageStore = { onMessage: vi.fn() };

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => authStore
}));
vi.mock('@/stores/agentMessage', () => ({
  useAgentMessageStore: () => messageStore
}));
vi.mock('@/utils/notification', () => ({
  showNotification: vi.fn()
}));
vi.mock('@/utils/toast', () => ({
  toast: { warning: vi.fn() }
}));
vi.mock('@/utils/websocket/url', () => ({
  resolveAgentWsUrl: () => 'ws://test/ws'
}));

class MockWebSocket {
  static readonly OPEN = 1;
  static readonly CONNECTING = 0;
  static last: MockWebSocket | null = null;

  readonly url: string;
  readyState = MockWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.last = this;
  }

  send() {}

  close() {
    this.readyState = 3;
    this.onclose?.({ code: 1000 });
  }
}

const originalWebSocket = globalThis.WebSocket;

describe('agent websocket OPEN barrier', () => {
  beforeEach(() => {
    vi.resetModules();
    authStore.isLoggedIn = true;
    MockWebSocket.last = null;
    Object.defineProperty(globalThis, 'WebSocket', {
      configurable: true,
      writable: true,
      value: MockWebSocket
    });
  });

  afterEach(async () => {
    const manager = await import('@/utils/websocket/manager');
    manager.closeAppWebSocket();
    Object.defineProperty(globalThis, 'WebSocket', {
      configurable: true,
      writable: true,
      value: originalWebSocket
    });
  });

  it('does not resolve until the socket reaches OPEN', async () => {
    const { ensureAppWebSocket } = await import('@/utils/websocket/manager');
    const opened = ensureAppWebSocket();
    const socket = MockWebSocket.last;
    expect(socket?.url).toBe('ws://test/ws');

    let settled = false;
    void opened.then(() => {
      settled = true;
    });
    await Promise.resolve();
    expect(settled).toBe(false);

    socket!.readyState = MockWebSocket.OPEN;
    socket!.onopen?.();
    await expect(opened).resolves.toBe(true);
  });
});
