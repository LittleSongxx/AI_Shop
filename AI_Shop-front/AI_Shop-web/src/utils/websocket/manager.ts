import { useAgentMessageStore } from '@/stores/agentMessage';
import { useAuthStore } from '@/stores/auth';
import { showNotification, type NotificationData } from '@/utils/notification';
import { toast } from '@/utils/toast';
import { resolveAgentWsUrl } from '@/utils/websocket/url';
import type { AgentSourceRef } from '@/utils/agentHistory';

export interface AppWsMessage {
  messageType?: 'agent' | 'notify' | string;
  messageId?: number | string;
  userMessage?: string;
  assistantMessage?: string;
  bizType?: string;
  bizId?: string;
  outPutType?: number;
  userId?: string;
  notificationId?: string;
  title?: string;
  content?: string;
  createTime?: string;
  sourceRefs?: AgentSourceRef[] | { sources?: AgentSourceRef[] };
  schemaVersion?: number | string;
  runId?: string;
  requestId?: string;
  episodeId?: string;
  eventId?: string;
  seq?: number | string;
  terminalState?: string;
  replayCursor?: string;
}

type UnreadRefreshHandler = () => void | Promise<void>;

let ws: WebSocket | null = null;
let maxRetries = 5;
let retryInterval = 2000;
let isConnecting = false;
let retryCount = 0;
let heartbeatTimer: ReturnType<typeof setInterval> | null = null;
const HEARTBEAT_INTERVAL = 5000;
let needReconnect = true;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let unreadRefreshHandler: UnreadRefreshHandler | null = null;
let openWaiters: Array<(opened: boolean) => void> = [];

const wsCheckEnabled = () => import.meta.env.VITE_WS_CHECK === 'true';

const settleOpenWaiters = (opened: boolean) => {
  if (!openWaiters.length) return;
  const waiters = openWaiters;
  openWaiters = [];
  waiters.forEach((resolve) => resolve(opened));
};

const isNotifyMessage = (data: AppWsMessage) =>
  data.messageType === 'notify' || Boolean(data.notificationId && data.title);

const handleNotifyMessage = (data: AppWsMessage) => {
  const payload: NotificationData = {
    notificationId: data.notificationId || '',
    title: data.title || '',
    content: data.content || '',
    bizType: data.bizType,
    bizId: data.bizId,
    createTime: data.createTime,
    readStatus: 0
  };
  if (payload.notificationId && payload.title) {
    showNotification(payload);
  }
  void unreadRefreshHandler?.();
};

const handleAgentMessage = (data: AppWsMessage) => {
  useAgentMessageStore().onMessage(data);
};

const dispatchWsMessage = (data: AppWsMessage) => {
  if (isNotifyMessage(data)) {
    handleNotifyMessage(data);
    return;
  }
  handleAgentMessage(data);
};

const clearHeartbeat = () => {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
};

const startHeartbeat = () => {
  clearHeartbeat();
  heartbeatTimer = setInterval(() => {
    if (ws?.readyState === WebSocket.OPEN) ws.send('ping');
  }, HEARTBEAT_INTERVAL);
};

const handleReconnect = () => {
  if (!needReconnect || isConnecting) return;
  if (retryCount >= maxRetries) {
    closeAppWebSocket();
    if (wsCheckEnabled()) {
      toast.warning('实时连接断开，请刷新页面或重新进入智能客服');
    }
    retryCount = 0;
    return;
  }
  retryCount += 1;
  isConnecting = false;
  const delay = retryInterval * Math.pow(1.5, retryCount - 1);
  if (wsCheckEnabled()) {
    console.warn(`WebSocket 重连: 第 ${retryCount} 次，${delay}ms 后重试`);
  }
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connectWs, delay);
};

const connectWs = () => {
  if (isConnecting || !needReconnect) return;
  const authStore = useAuthStore();
  if (!authStore.isLoggedIn) {
    settleOpenWaiters(false);
    return;
  }

  isConnecting = true;
  const wsUrl = resolveAgentWsUrl();
  try {
    ws = new WebSocket(wsUrl);
    ws.onopen = () => {
      isConnecting = false;
      retryCount = 0;
      startHeartbeat();
      settleOpenWaiters(true);
    };
    ws.onmessage = (event) => {
      const raw = typeof event.data === 'string' ? event.data.trim() : '';
      if (!raw || raw === 'ping' || raw === 'pong') return;
      try {
        dispatchWsMessage(JSON.parse(raw) as AppWsMessage);
      } catch {

      }
    };
    ws.onerror = () => handleReconnect();
    ws.onclose = (event) => {
      isConnecting = false;
      clearHeartbeat();
      if (event.code !== 1000) {
        handleReconnect();
      } else {
        settleOpenWaiters(false);
      }
    };
  } catch {
    isConnecting = false;
    handleReconnect();
  }
};

export const ensureAppWebSocket = (): Promise<boolean> => {
  if (ws?.readyState === WebSocket.OPEN) return Promise.resolve(true);

  const authStore = useAuthStore();
  if (!authStore.isLoggedIn) return Promise.resolve(false);

  const opened = new Promise<boolean>((resolve) => {
    openWaiters.push(resolve);
  });
  if (ws?.readyState !== WebSocket.CONNECTING) {
    needReconnect = true;
    connectWs();
  }
  return opened;
};

// Explicit name for callers that want to document the OPEN barrier while the
// legacy ensureAppWebSocket name remains source-compatible.
export const waitForAppWebSocket = ensureAppWebSocket;

export const initAppWebSocket = (options?: { force?: boolean }) => {
  if (options?.force) closeAppWebSocket();
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

  const authStore = useAuthStore();
  if (!authStore.isLoggedIn) return;

  needReconnect = true;
  connectWs();
};

export const closeAppWebSocket = () => {
  needReconnect = false;
  isConnecting = false;
  settleOpenWaiters(false);
  clearHeartbeat();
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    ws.close();
  }
  ws = null;
};

export const bindUnreadRefreshHandler = (handler: UnreadRefreshHandler | null) => {
  unreadRefreshHandler = handler;
};

export const initAgentWebSocket = initAppWebSocket;

export const closeAgentWebSocket = closeAppWebSocket;
