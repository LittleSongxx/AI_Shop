

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
  imageAssetId?: string;
  imageSnapshot?: Record<string, unknown>;
  selectedVisualSubject?: Record<string, unknown>;
  assistantMessage: string;
  status: number;
  bizType?: string;
  bizData?: string | null;
  sendTime?: string;
  sourceRefs?: AgentSourceRef[];
  messageType?: string;
  schemaVersion?: number;
  runId?: string;
  requestId?: string;
  episodeId?: string;
  eventId?: string;
  seq?: number;
  terminalState?: string;
  replayCursor?: string;
}

type StreamAccumulator = {
  chunks: Map<number, string>;
  seenEvents: Set<string>;
  seenSequences: Set<number>;
  terminal: boolean;
};

// Keep ordering/dedupe state outside the public history shape.  This avoids
// leaking reducer bookkeeping into Vue templates or persisted history rows.
// Key by the durable message ID rather than object identity: history merges
// intentionally clone rows while a live stream may still be arriving.
const streamAccumulators = new Map<number, StreamAccumulator>();
const MAX_STREAM_ACCUMULATORS = 512;

const streamAccumulatorFor = (message: AgentHistoryMessage): StreamAccumulator => {
  let accumulator = streamAccumulators.get(message.messageId);
  if (!accumulator) {
    accumulator = {
      chunks: new Map(),
      seenEvents: new Set(),
      seenSequences: new Set(),
      terminal: false
    };
    if (streamAccumulators.size >= MAX_STREAM_ACCUMULATORS) {
      const oldest = streamAccumulators.keys().next().value;
      if (oldest != null) streamAccumulators.delete(oldest);
    }
    streamAccumulators.set(message.messageId, accumulator);
  }
  return accumulator;
};

const parsePositiveSequence = (value: unknown): number | undefined => {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
};

const copyStreamEnvelope = (
  target: AgentHistoryMessage,
  payload: AgentStreamPayload | Record<string, unknown>
) => {
  const source = payload as Record<string, unknown>;
  const schemaVersion = Number(source.schemaVersion ?? source.schema_version);
  const sequence = parsePositiveSequence(source.seq ?? source.sequence);
  const advancesCursor = sequence == null || sequence >= Number(target.seq || 0);
  if (Number.isFinite(schemaVersion) && schemaVersion > 0) target.schemaVersion = schemaVersion;
  if (source.runId != null || source.run_id != null) {
    target.runId = String(source.runId ?? source.run_id);
  }
  if (source.requestId != null || source.request_id != null) {
    target.requestId = String(source.requestId ?? source.request_id);
  }
  if (source.episodeId != null || source.episode_id != null) {
    target.episodeId = String(source.episodeId ?? source.episode_id);
  }
  if (advancesCursor && (source.eventId != null || source.event_id != null)) {
    target.eventId = String(source.eventId ?? source.event_id);
  }
  if (sequence != null && advancesCursor) target.seq = sequence;
  if (source.terminalState != null || source.terminal_state != null) {
    target.terminalState = String(source.terminalState ?? source.terminal_state);
  }
  if (advancesCursor && (source.replayCursor != null || source.replay_cursor != null)) {
    target.replayCursor = String(source.replayCursor ?? source.replay_cursor);
  }
};

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
  const schemaVersionRaw = pickField(raw, 'schemaVersion', 'schema_version');
  const runIdRaw = pickField(raw, 'runId', 'run_id');
  const requestIdRaw = pickField(raw, 'requestId', 'request_id');
  const episodeIdRaw = pickField(raw, 'episodeId', 'episode_id');
  const eventIdRaw = pickField(raw, 'eventId', 'event_id');
  const seqRaw = pickField(raw, 'seq', 'sequence');
  const terminalStateRaw = pickField(raw, 'terminalState', 'terminal_state');
  const replayCursorRaw = pickField(raw, 'replayCursor', 'replay_cursor');
  const imageAssetIdRaw = pickField(raw, 'imageAssetId', 'image_asset_id');
  const imageSnapshotRaw = pickField(raw, 'imageSnapshot', 'image_snapshot_json');
  const selectedVisualSubjectRaw = pickField(
    raw,
    'selectedVisualSubject',
    'selected_visual_subject_json'
  );

  return {
    messageId: Number.isFinite(parsedId) && parsedId > 0 ? parsedId : 0,
    userMessage: userRaw != null ? String(userRaw) : undefined,
    imageAssetId: imageAssetIdRaw != null ? String(imageAssetIdRaw) : undefined,
    imageSnapshot: imageSnapshotRaw && typeof imageSnapshotRaw === 'object'
      ? imageSnapshotRaw as Record<string, unknown>
      : undefined,
    selectedVisualSubject: selectedVisualSubjectRaw && typeof selectedVisualSubjectRaw === 'object'
      ? selectedVisualSubjectRaw as Record<string, unknown>
      : undefined,
    assistantMessage: assistantRaw != null ? String(assistantRaw) : '',
    status: statusRaw != null ? Number(statusRaw) : 2,
    bizType: bizTypeRaw != null ? String(bizTypeRaw) : undefined,
    bizData: bizDataRaw != null ? String(bizDataRaw) : null,
    sendTime: sendTimeRaw != null ? String(sendTimeRaw) : undefined,
    sourceRefs,
    messageType: messageTypeRaw != null ? String(messageTypeRaw) : undefined,
    schemaVersion: schemaVersionRaw != null ? Number(schemaVersionRaw) : undefined,
    runId: runIdRaw != null ? String(runIdRaw) : undefined,
    requestId: requestIdRaw != null ? String(requestIdRaw) : undefined,
    episodeId: episodeIdRaw != null ? String(episodeIdRaw) : undefined,
    eventId: eventIdRaw != null ? String(eventIdRaw) : undefined,
    seq: parsePositiveSequence(seqRaw),
    terminalState: terminalStateRaw != null ? String(terminalStateRaw) : undefined,
    replayCursor: replayCursorRaw != null ? String(replayCursorRaw) : undefined
  };
};

export interface AgentStreamPayload {
  messageId?: number | string;
  userMessage?: string;
  imageAssetId?: string;
  imageSnapshot?: Record<string, unknown>;
  selectedVisualSubject?: Record<string, unknown>;
  assistantMessage?: string;
  bizType?: string;
  bizData?: string | null;
  outPutType?: number;
  sendTime?: string;
  sourceRefs?: AgentSourceRef[] | { sources?: AgentSourceRef[] };
  messageType?: string;
  schemaVersion?: number | string;
  runId?: string;
  requestId?: string;
  episodeId?: string;
  eventId?: string;
  seq?: number | string;
  terminalState?: string;
  replayCursor?: string;
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
  const terminalState = String(payload.terminalState || '').toUpperCase();
  const terminal =
    outputType === 1 ||
    outputType === 2 ||
    ['SUCCEEDED', 'FAILED', 'CANCELLED', 'INCONCLUSIVE', 'MANUAL_REVIEW'].includes(
      terminalState
    );
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

  const accumulator = streamAccumulatorFor(message);
  const sequence = parsePositiveSequence(payload.seq);
  const eventId = payload.eventId ? String(payload.eventId) : undefined;
  // A terminal history row is the reconciliation source of truth.  Ignore
  // any late pub/sub frame, including legacy frames without envelope fields.
  if (accumulator.terminal) {
    return { message, created: false, terminal: false };
  }
  const hasEnvelopeOrdering = sequence != null || !!eventId;
  if (hasEnvelopeOrdering) {
    if (eventId && accumulator.seenEvents.has(eventId)) {
      return { message, created: false, terminal: false };
    }
    if (eventId) accumulator.seenEvents.add(eventId);
    if (sequence != null) {
      // A sequence is unique within a run.  This also handles duplicate
      // delivery from Redis when an event ID was not preserved by a proxy.
      if (accumulator.seenSequences.has(sequence)) {
        return { message, created: false, terminal: false };
      }
      accumulator.seenSequences.add(sequence);
    }
  }

  copyStreamEnvelope(message, payload);

  if (payload.userMessage != null && payload.userMessage !== '') {
    message.userMessage = String(payload.userMessage);
  }
  if (payload.imageAssetId) message.imageAssetId = String(payload.imageAssetId);
  if (payload.imageSnapshot && typeof payload.imageSnapshot === 'object') {
    message.imageSnapshot = payload.imageSnapshot;
  }
  if (payload.selectedVisualSubject && typeof payload.selectedVisualSubject === 'object') {
    message.selectedVisualSubject = payload.selectedVisualSubject;
  }
  if (payload.bizType) message.bizType = payload.bizType;
  if (payload.bizData != null) message.bizData = payload.bizData;
  if (payload.sendTime) message.sendTime = payload.sendTime;
  if (payload.messageType) message.messageType = payload.messageType;

  const refs = normalizeSourceRefs(payload.sourceRefs);
  if (refs.length || payload.sourceRefs != null) message.sourceRefs = refs;

  if (outputType === 2 || terminalState === 'FAILED') {
    message.assistantMessage = payload.assistantMessage || '服务器返回错误，请联系管理员';
    message.status = 2;
    accumulator.terminal = true;
  } else if (terminal) {
    if (payload.assistantMessage != null && payload.assistantMessage.trim() !== '') {
      // A terminal frame is authoritative and may contain the complete
      // response rather than the individual deltas.
      message.assistantMessage = payload.assistantMessage;
    }
    message.status = 2;
    accumulator.terminal = true;
  } else if (message.status === 1) {
    if (sequence != null) {
      accumulator.chunks.set(sequence, payload.assistantMessage || '');
      message.assistantMessage = [...accumulator.chunks.entries()]
        .sort(([left], [right]) => left - right)
        .map(([, chunk]) => chunk)
        .join('');
    } else {
      message.assistantMessage += payload.assistantMessage || '';
    }
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
    if (incoming.status !== 1 || incoming.terminalState) {
      streamAccumulatorFor(incoming).terminal = true;
    }
    return incoming;
  }
  if (incoming.userMessage) existing.userMessage = incoming.userMessage;
  if (incoming.imageAssetId) existing.imageAssetId = incoming.imageAssetId;
  if (incoming.imageSnapshot) existing.imageSnapshot = incoming.imageSnapshot;
  if (incoming.selectedVisualSubject) existing.selectedVisualSubject = incoming.selectedVisualSubject;
  if (incoming.bizType) existing.bizType = incoming.bizType;
  if (incoming.bizData != null) existing.bizData = incoming.bizData;
  if (incoming.sendTime) existing.sendTime = incoming.sendTime;
  if (incoming.sourceRefs?.length) existing.sourceRefs = incoming.sourceRefs;
  if (incoming.schemaVersion != null) existing.schemaVersion = incoming.schemaVersion;
  if (incoming.runId) existing.runId = incoming.runId;
  if (incoming.requestId) existing.requestId = incoming.requestId;
  if (incoming.episodeId) existing.episodeId = incoming.episodeId;
  if (incoming.eventId) existing.eventId = incoming.eventId;
  if (incoming.seq != null) existing.seq = incoming.seq;
  if (incoming.terminalState) existing.terminalState = incoming.terminalState;
  if (incoming.replayCursor) existing.replayCursor = incoming.replayCursor;
  if (incoming.status !== 1 || incoming.terminalState) {
    streamAccumulatorFor(existing).terminal = true;
  }
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
