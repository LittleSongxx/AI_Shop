

export interface AgentSourceRef {
  type?: string;
  questionId?: number | string;
  documentId?: number | string;
  chunkId?: string;
  title?: string;
  question?: string;
  heading?: string;
  snippet?: string;
  source?: string;
  version?: number | string;
  retrieval?: string;
  url?: string;
}

export interface AgentHistoryMessage {
  messageId: number;
  userMessage?: string;
  assistantMessage: string;
  status: number;
  bizType?: string;
  bizData?: string | null;
  sendTime?: string;
  sourceRefs?: AgentSourceRef[];
  messageType?: string;
}

const pickField = (raw: Record<string, unknown>, ...keys: string[]) => {
  for (const key of keys) {
    const val = raw[key];
    if (val != null && val !== '') return val;
  }
  return undefined;
};

export const normalizeSourceRefs = (raw: unknown): AgentSourceRef[] => {
  const value = raw && !Array.isArray(raw) && typeof raw === 'object'
    ? (raw as Record<string, unknown>).sources
    : raw;
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is AgentSourceRef => !!item && typeof item === 'object');
};

export const extractHistoryPage = (res: unknown) => {
  if (Array.isArray(res)) {
    return { list: res, pageNo: 1, pageTotal: 1, totalCount: res.length };
  }

  let page: Record<string, unknown> | null = null;
  if (res && typeof res === 'object') {
    page = res as Record<string, unknown>;
    if (page.data && typeof page.data === 'object' && !Array.isArray(page.list)) {
      page = page.data as Record<string, unknown>;
    }
  }

  if (!page) {
    return { list: [], pageNo: 1, pageTotal: 1, totalCount: 0 };
  }

  const list = Array.isArray(page.list)
    ? page.list
    : Array.isArray(page.records)
      ? page.records
      : Array.isArray(page.rows)
        ? page.rows
        : [];

  return {
    list,
    pageNo: Number(page.pageNo) || 1,
    pageTotal: Number(page.pageTotal) || 1,
    totalCount: Number(page.totalCount) || list.length
  };
};

export const normalizeAgentHistoryMessage = (raw: Record<string, unknown>): AgentHistoryMessage => {
  const messageIdRaw = pickField(raw, 'messageId', 'message_id');
  const parsedId = messageIdRaw != null ? Number(messageIdRaw) : 0;
  const assistantRaw = pickField(raw, 'assistantMessage', 'assistant_message');
  const userRaw = pickField(raw, 'userMessage', 'user_message');
  const bizTypeRaw = pickField(raw, 'bizType', 'biz_type');
  const bizDataRaw = pickField(raw, 'bizData', 'biz_data');
  const sendTimeRaw = pickField(raw, 'sendTime', 'send_time');
  const statusRaw = pickField(raw, 'status');
  const sourceRefs = normalizeSourceRefs(pickField(raw, 'sourceRefs', 'source_refs'));
  const messageTypeRaw = pickField(raw, 'messageType', 'message_type');

  return {
    messageId: Number.isFinite(parsedId) && parsedId > 0 ? parsedId : 0,
    userMessage: userRaw != null ? String(userRaw) : undefined,
    assistantMessage: assistantRaw != null ? String(assistantRaw) : '',
    status: statusRaw != null ? Number(statusRaw) : 2,
    bizType: bizTypeRaw != null ? String(bizTypeRaw) : undefined,
    bizData: bizDataRaw != null ? String(bizDataRaw) : null,
    sendTime: sendTimeRaw != null ? String(sendTimeRaw) : undefined,
    sourceRefs,
    messageType: messageTypeRaw != null ? String(messageTypeRaw) : undefined
  };
};

export interface AgentStreamPayload {
  messageId?: number | string;
  userMessage?: string;
  assistantMessage?: string;
  bizType?: string;
  bizData?: string | null;
  outPutType?: number;
  sendTime?: string;
  sourceRefs?: AgentSourceRef[] | { sources?: AgentSourceRef[] };
  messageType?: string;
}

export interface AgentUpsertResult {
  message: AgentHistoryMessage;
  created: boolean;
  terminal: boolean;
}

export const upsertAgentStreamMessage = (
  list: AgentHistoryMessage[],
  payload: AgentStreamPayload
): AgentUpsertResult | null => {
  const parsedId = Number(payload.messageId);
  if (!Number.isFinite(parsedId) || parsedId <= 0) return null;

  const outputType = Number(payload.outPutType ?? 0);
  const terminal = outputType === 1 || outputType === 2;
  let message = list.find((item) => String(item.messageId) === String(parsedId));
  const created = !message;
  if (!message) {
    message = {
      messageId: parsedId,
      assistantMessage: '',
      status: terminal ? 2 : 1
    };
    list.push(message);
  }

  if (payload.userMessage != null && payload.userMessage !== '') {
    message.userMessage = String(payload.userMessage);
  }
  if (payload.bizType) message.bizType = payload.bizType;
  if (payload.bizData != null) message.bizData = payload.bizData;
  if (payload.sendTime) message.sendTime = payload.sendTime;
  if (payload.messageType) message.messageType = payload.messageType;

  const refs = normalizeSourceRefs(payload.sourceRefs);
  if (refs.length || payload.sourceRefs != null) message.sourceRefs = refs;

  if (outputType === 2) {
    message.assistantMessage = payload.assistantMessage || '服务器返回错误，请联系管理员';
    message.status = 2;
  } else if (outputType === 1) {
    if (payload.assistantMessage != null && payload.assistantMessage.trim() !== '') {
      message.assistantMessage = payload.assistantMessage;
    }
    message.status = 2;
  } else if (message.status === 1) {
    message.assistantMessage += payload.assistantMessage || '';
  }

  return { message, created, terminal };
};

export const upsertAgentHttpMessage = (
  list: AgentHistoryMessage[],
  raw: Record<string, unknown>
): AgentHistoryMessage | null => {
  const incoming = normalizeAgentHistoryMessage(raw);
  if (!incoming.messageId) return null;
  const existing = list.find((item) => item.messageId === incoming.messageId);
  if (!existing) {
    list.push(incoming);
    return incoming;
  }
  if (incoming.userMessage) existing.userMessage = incoming.userMessage;
  if (incoming.bizType) existing.bizType = incoming.bizType;
  if (incoming.bizData != null) existing.bizData = incoming.bizData;
  if (incoming.sendTime) existing.sendTime = incoming.sendTime;
  if (incoming.sourceRefs?.length) existing.sourceRefs = incoming.sourceRefs;
  if (existing.status === 1 && incoming.assistantMessage) {
    existing.assistantMessage = incoming.assistantMessage;
  }
  return existing;
};

export const sortHistoryMessages = (list: AgentHistoryMessage[]) =>
  [...list].sort((a, b) => a.messageId - b.messageId);

export const mergeHistoryMessages = (
  existing: AgentHistoryMessage[],
  incoming: AgentHistoryMessage[]
) => {
  const map = new Map<number, AgentHistoryMessage>();
  existing.forEach((item) => {
    if (item.messageId) map.set(item.messageId, item);
  });
  incoming.forEach((item) => {
    if (!item.messageId) return;
    const previous = map.get(item.messageId);
    map.set(item.messageId, previous ? { ...previous, ...item } : item);
  });
  return sortHistoryMessages([...map.values()]);
};
